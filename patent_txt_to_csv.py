"""
Script designed to take a directory of APS format TXT files provided by the USPTO and convert
them to a SQLite database.

Note we attempted to parallelize this process, but because SQLite inserts must necessarily be
performed serially we had minimal performance gains.
"""

import argparse  # Takes command line arguments
import csv  # Handles CSV output
import logging  # Handles logging output
import yaml  # Takes input files
import re  # Regular expressions
import shutil  # Handles system paths
import sqlite3  # Handles sqlite

from collections import defaultdict  # Dictionaries that provide default values
from pathlib import Path  # Feature-rich path objects
from pprint import pformat  # Prints data in a nice way

try:
    from termcolor import colored  # Allows colored terminal output
except ImportError:
    logging.debug("termcolor not available")

    def colored(text, _color):
        """
        Dummy function in case termcolor is not available
        """
        return text

# Dictionary of files containing document numbers to ignore
ENTRIES_TO_IGNORE = {
    "pftaps19871103_wk44.txt": [
        "047029323",  # These overlap with October 27th
        "047029382",
    ],

    "pftaps19871110_wk45.txt": [
        "H00003670",  # These overlap with November 3rd
        "H00003689",
        "H00003743",
        "047035218",
        "047035404",
        "047035781",
        "047031492",  # These overlap with October 27th
        "047031883",
        "047032049",
        "047032634",
        "047032952",
        "047033185",
        "047033908",
        "047033894",
        "047034327",
        "047034335",
        "047034491",
        "047034653",
        "047035170",
    ]
}


class PatentTxtToTabular:
    """
    Main object for the conversion. All meaningful computation takes place within this object.
    """
    def __init__(self, txt_input, config, output_path, output_type, logger, clean, joiner, **kwargs, ):
        """
        Initializes the converter

        txt_input: Single TXT file or path to directory of TXT files
        config: YAML configuration file linking APS fields to table and column names
        output_path: Path to output directory, will either hold the SQLite database or collection of CSV files
        output_type: Either "sqlite" or "csv"
        logger: Logger object
        clean: Whether to clean the output directory before writing; if not clean, existing files are appended
        joiner: String to join multiple values together
        """
        # Passes the logger object to the class
        self.logger = logger

        # Sets the default joiner
        self.default_joiner = joiner

        # Gets list of TXT files to parse
        self.txt_files = []
        for input_path in txt_input:
            for path in expand_paths(input_path):
                if path.is_file():
                    self.txt_files.append(path)
                elif path.is_dir():
                    self.txt_files.extend(
                        path.glob(f'{"**/" if kwargs["recurse"] else ""}*.[tT][xX][tT]')
                    )
                else:
                    self.logger.fatal("specified input is invalid")
                    exit(1)

        # Make sure output path is valid before parsing so we don't waste
        # all that time!
        self.output_path = Path(output_path)

        # Delete existing contents if we want a new run
        if clean:
            shutil.rmtree(self.output_path)
        self.output_path.mkdir(parents=True, exist_ok=True)

        # Output to csv or sqlite
        self.output_type = output_type

        # Import paths from YAML file
        self.config = yaml.safe_load(open(config))
        self.fieldnames = self.get_fieldnames()

        # Tracks primary keys and value tables
        self.init_cache_vars()

        # Initialize the database if using SQLite output
        if self.output_type == "sqlite":
            try:
                from sqlite_utils import Database as SqliteDB
                self.db_path = (self.output_path / "db.sqlite").resolve()
                if self.db_path.exists():
                    self.logger.warning(
                        colored(
                            "Sqlite database %s exists; records will be appended.",
                            "yellow",
                        ),
                        self.db_path,
                    )
                db_conn = sqlite3.connect(str(self.db_path), isolation_level=None)
                db_conn.execute("pragma syncronous=off;")
                db_conn.execute("pragma journal_mode=memory;")
                self.db = SqliteDB(db_conn)

            except ImportError:
                logger.debut("sqlite_utils (pip3 install sqlite-utils) not available")
                raise

    def init_cache_vars(self):
        """
        Initializes empty dictionaries for storing output
        """
        self.tables = defaultdict(list)
        self.table_pk_idx = defaultdict(lambda: defaultdict(int))

    def yield_txt_doc(self, filepath):
        """
        Given a TXT file path, this iterates through the results and yields all data for a single patent document--
        i.e., it splits the file based on "PATN" headers. Each line within the document is stored as a string
        in the yielded list
        """
        # List for storing text
        txt_doc = []

        # Opens lines from file
        with open(filepath, "r", encoding="ISO-8859-1") as _fh:

            # Skip first line of header information
            next(_fh)

            # Splits file at each PATN line
            for i, line in enumerate(_fh):
                # When you get to the next patent, yield the current results
                # and restart
                if line.startswith("PATN"):
                    if txt_doc:
                        yield i - len(txt_doc), "".join(txt_doc)
                    txt_doc = []

                # Add line to current document
                txt_doc.append(line)

            # Make sure you yield the final document!
            yield i - len(txt_doc), "".join(txt_doc)

    def convert(self):
        """
        Once the object has been initialized, this method reads the data from the TXT file
        and maps it to the relevant field names. It then writes the data to CSV or SQLite.
        """
        if not self.txt_files:
            self.logger.warning(colored("No input files to prcoess!", "red", ))

        # Process each input file
        for input_file in self.txt_files:

            self.logger.info(colored("Processing %s...", "green"), input_file.resolve())
            self.current_filename = input_file.resolve().name

            for i, (linenum, doc) in enumerate(self.yield_txt_doc(input_file)):
                if i % 100 == 0:
                    self.logger.debug(
                        colored("Processing document %d...", "cyan"), i + 1
                    )

                self.process_doc(doc)

            self.logger.info(colored("...%d records processed!", "green"), i + 1)

            # Write the output to CSV or SQLite after each file
            self.flush_to_disk()

        self.logger.info(colored("Parsing complete!", "green"))

    def flush_to_disk(self):
        """
        Writes the output to CSV or SQLite depending on output-type flag
        """
        if self.output_type == "csv":
            self.write_csv_files()
        if self.output_type == "sqlite":
            self.write_sqlitedb()

        # Reset the cache
        self.init_cache_vars()

    def get_fieldnames(self):
        """
        Retrieves mapping from APS paths to field names based on config.yaml file
        passed during object initialization.

        Returns dictionary of dictionaries, where outer dictionary is keyed by
        four-letter APS headers, the inner dictionary is keyed by three-letter
        APS subheaders, and the values are the name of the field in the database.
        """
        fieldnames = defaultdict(list)

        def add_fieldnames(config, _fieldnames, parent_entity=None):
            """
            Internal function that is used recursively to dig down into recursive entities
            """
            if isinstance(config, str):
                if ":" in config:
                    _fieldnames.append(config.split(":")[0])
                    return
                _fieldnames.append(config)
                return

            if "<fieldname>" in config:
                _fieldnames.append(config["<fieldname>"])
                return

            if "<constant>" in config:
                _fieldnames.append(config["<constant>"]["<fieldname>"])
                return

            # If there's a new entity, we save the ids and recursively
            # fetch the fieldnames in the substructure
            if "<entity>" in config:
                entity = config["<entity>"]
                _fieldnames = []
                if parent_entity:
                    _fieldnames.append(f"{parent_entity}_id")
                if "<filename_field>" in config:
                    _fieldnames.append(config["<filename_field>"])
                for subconfig in config["<fields>"].values():
                    add_fieldnames(subconfig, _fieldnames, entity)

                # Since different paths may go to same table, append to
                # list of fieldnames here.
                fieldnames[entity] = list(
                    dict.fromkeys(fieldnames[entity] + _fieldnames).keys()
                )
                return

            if isinstance(config, list):
                for subconfig in config:
                    add_fieldnames(subconfig, _fieldnames, parent_entity)
                return

            raise LookupError(
                "Invalid configuration:"
                + "\n "
                + "\n ".join(pformat(config).split("\n"))
            )

        for config in self.config.values():
            add_fieldnames(config, [])

        for entity in fieldnames:
            if entity != "patent":
                fieldnames[entity] = ["patent_id"] + fieldnames[entity]
            fieldnames[entity] = ["id"] + fieldnames[entity]

        return fieldnames

    def new_record(self, subconfig):
        """
        Generates a new record object based on the parameters in subconfig
        """
        # Generate a new record
        record = {}

        # If there is at least one constant field
        if "<constant>" in subconfig:

            # Add each constant field to the record
            for variable in subconfig["<constant>"]:
                fieldname = variable["<fieldname>"]
                value = variable["<enum_type>"]
                record[fieldname] = value

        # If we want to save the filename, save it now
        if "<filename_field>" in subconfig:
            fieldname = subconfig["<filename_field>"]
            record[fieldname] = self.current_filename

        return record

    def process_doc(self, txt_doc):
        """The method for actually reading the contents of the CSV files"""
        # Initialize with PATN since we know first section of document will
        # be a patent.
        header = "PATN"
        last_header = header
        current_entity = self.config[header]['<entity>']
        subconfig = self.config[header]['<fields>']
        splitter = None
        patent_pk = None
        pk_counter = 0
        record = self.new_record(subconfig)

        if "<primary_key>" in self.config[header]:
            pk_head = self.config[header]["<primary_key>"]

        # Go through each line of the file
        # Need to skip first two lines since they contain metadata
        # and we don't want to write an empty patent record
        for line in txt_doc.split('\n')[1:]:

            # Get the first four characters to see if we're in a new logical unit
            header = line[0:4].strip()

            # Keeps track of the last known header for evaluating whether multi-line
            # statements are relevant
            if header:
                last_header = header

            if header == pk_head:
                # Primary keys must be unique
                assert "id" not in record
                record["id"] = line[4:].strip()
                patent_pk = record["id"]

            if len(header) == 4:
                # Change the header and current config if so
                # If we care about the new section, write what we've found to a file
                # and fetch the new subsections
                if header in self.config:
                    self.tables[current_entity].append(record)
                    current_entity = self.config[header]['<entity>']
                    subconfig = self.config[header]['<fields>']
                    record = self.new_record(subconfig)

                    record["id"] = str(patent_pk) + '_' + str(pk_counter)
                    record["patent_id"] = patent_pk
                    pk_counter += 1

                    # Since headers don't cross entities, any starting blank lines
                    # shouldn't be appended to the previous record
                    last_header = ""

                # If we don't care about the new section, just say it has no relevant
                # fields and continue. Don't create a new record or write yet, since that
                # will create empty lines for the null section
                else:
                    subconfig = {}

            # If there's no header but we're in a meaningful subconfig, we're continuing the
            # same script as the previous line. Just keep appending.
            elif not header and any(re.match(entry, last_header) for entry in subconfig):
                # Fieldname must have been previously defined if last_header in subconfig
                # Append it for each field where that header was relevant
                for fieldname in fieldnames:
                    record[fieldname] = record[fieldname] + ' ' + line[4:].strip()
            else:
                # List for holding names if data goes in multiple fields
                fieldnames = []

                for entry in subconfig:
                    # If the config file entry matches the file header,
                    # pull the fieldname from the YAML file
                    if re.match(entry, header):
                        # Get the text to store
                        value = line[4:].strip()

                        # If the value is simply the fieldname, it's one-to-one
                        # and we can just save the value
                        if isinstance(subconfig[entry], str):
                            fieldname = subconfig[entry]
                            fieldnames.append(fieldname)
                            if fieldname in record:
                                self.logger.debug(
                                    colored("No joiner specified for %s, using default.", "yellow"),
                                    fieldname
                                )
                                record[fieldname] = record[fieldname] + self.default_joiner + value
                            else:
                                record[fieldname] = value

                        # If the value is parameterized, we need to handle
                        # many-to-one issues
                        elif "<fieldname>" in subconfig[entry]:
                            # First, save the fieldname
                            fieldname = subconfig[entry]["<fieldname>"]
                            fieldnames.append(fieldname)

                            if "<splitter>" in subconfig[entry]:
                                splitter = subconfig[entry]["<splitter>"]
                            else:
                                splitter = None

                            # If we've seen one before, add the new one with a delimiter
                            if fieldname in record:
                                # Pulls the joiner if there is one, otherwise uses default
                                if "<joiner>" in subconfig[entry]:
                                    joiner = subconfig[entry]["<joiner>"]
                                else:
                                    joiner = self.default_joiner

                                # If new occurances get their own row
                                if joiner == "<new_record>":

                                    # Write the previous record to the file
                                    self.tables[current_entity].append(record)

                                    # Generate a new record with keys
                                    record = self.new_record(subconfig)
                                    record["id"] = str(patent_pk) + '_' + str(pk_counter)
                                    record["patent_id"] = str(patent_pk)
                                    pk_counter += 1

                                    # Record the new value
                                    record[fieldname] = value

                                # If we're using a text joiner
                                else:
                                    record[fieldname] = record[fieldname] + joiner + value
                            # Otherwise, just save the value for now
                            else:
                                record[fieldname] = value

                        elif "<constant>" in subconfig[entry]:
                            # NOTE: MAD HACKY CODE HERE
                            # This should reasonably have a line
                            # fieldname = subconfig[entry]["<constant>"]["<fieldname>"]
                            # and then just set the record for fieldname
                            # However, the way we continue appending in the case of headerless
                            # rows depends on maintaining the previous fieldname, which we don't
                            # want to do to constant fields. Therefore, we don't use fieldname here
                            value = subconfig[entry]["<constant>"]["<enum_type>"]
                            record[subconfig[entry]["<constant>"]["<fieldname>"]] = value

                        else:
                            print("ERROR: Fields must be string or contain <fieldname> or <constant>")
                            raise LookupError
                    elif splitter:
                        if re.match(splitter, header):
                            self.tables[current_entity].append(record)

                            record = self.new_record(subconfig)

                            record["id"] = str(patent_pk) + '_' + str(pk_counter)
                            record["patent_id"] = patent_pk
                            pk_counter += 1

        # Add to list of those entities found so far
        self.tables[current_entity].append(record)

    def write_csv_files(self):
        """
        Given the parsed results stored in self.tables, write the results to the CSV files
        """
        self.logger.info(
            colored("writing csv files to %s ...", "green"), self.output_path.resolve()
        )

        for tablename, rows in self.tables.items():
            output_file = self.output_path / f"{tablename}.csv"

            records_to_add = self.filter_records(tablename, rows)
            
            if output_file.exists():
                self.logger.debug(
                    colored("CSV file %s exists; records will be appended.", "yellow"),
                    output_file
                )

                with output_file.open("a", newline='') as _fh:
                    writer = csv.DictWriter(_fh, fieldnames=self.fieldnames[tablename])
                    writer.writerows(records_to_add)

            else:
                with output_file.open("w", newline='') as _fh:
                    writer = csv.DictWriter(_fh, fieldnames=self.fieldnames[tablename])
                    writer.writeheader()
                    writer.writerows(records_to_add)

    def write_sqlitedb(self):
        """
        Given the results stored in self.tables, write the results to db.sqlite
        """
        self.logger.info(
            colored("Writing records to %s ...", "green"), self.db_path,
        )
        self.db.conn.execute("begin exclusive;")
        for tablename, rows in self.tables.items():
            params = {"column_order": self.fieldnames[tablename], "alter": True}
            if "id" in self.fieldnames[tablename]:
                params["not_null"] = {"id"}
            self.logger.debug(
                colored("Writing %d records to `%s`...", "magenta"),
                len(rows),
                tablename,
            )

            # Ignore ENTRIES_TO_IGNORE
            records_to_add = self.filter_records(tablename, rows)

            # For some reason we need to really limit the batch size
            # or else you end up running into SQL variable limits somehow?
            self.db[tablename].insert_all(records_to_add, batch_size=20, **params)

    def filter_records(self, tablename, rows):
        """
        Given a list of records, filter out those that are in the ENTRIES_TO_IGNORE.
        These records are confirmed by the USPTO to be errors.
        """
        # We want to ignore some records that are in the data by mistake
        # First, check if the current file contains any ignored entries
        if self.current_filename in ENTRIES_TO_IGNORE:
            # Get the list of entries to ignore
            docs_to_ignore = ENTRIES_TO_IGNORE[self.current_filename]

            # Create list of records to include in output
            records_to_add = []

            # Check if each row should be included
            for row in rows:
                # If it's a child document and we care about the parent, append it
                if "patent_id" in row and row["patent_id"] not in docs_to_ignore:
                    records_to_add.append(row)

                # If it's the main patent entry and we care about it, append it
                elif tablename == "patent" and row["id"] not in docs_to_ignore:
                    records_to_add.append(row)

        # If we care about all records in the file, just add all the rows
        else:
            records_to_add = rows

        return records_to_add
    

def expand_paths(path_expr):
    """
    Gets all files of subdirectories of given path expression
    """
    path = Path(path_expr).expanduser()
    return Path(path.root).glob(
        str(Path("").joinpath(*path.parts[1:] if path.is_absolute() else path.parts))
    )


def main():
    """Takes arguments from command line"""
    arg_parser = argparse.ArgumentParser(description="Description: {}".format(__file__))

    arg_parser.add_argument(
        '-v', "--verbose", action="store_true", default=False, help="increase verbosity"
    )

    arg_parser.add_argument(
        '-q', "--quiet", action="store_true", default=False, help="quiet operation"
    )

    arg_parser.add_argument(
        '-j', "--joiner", action="store", default="|#|", help="Default joiner for many-to-one fields"
    )

    arg_parser.add_argument(
        '-i',
        "--txt-input",
        action="store",
        nargs="+",
        required=True,
        help="TXT file or directory of TXT files (*.{txt, TXT}) to parse recursively"
             "(multiple arguments can be passed",
    )

    arg_parser.add_argument(
        '-r',
        "--recurse",
        action="store_true",
        help="if supplied, the parser will search subdirectories for"
             " TXT files (*.{txt, TXT}) to parse",
    )

    arg_parser.add_argument(
        '-c',
        "--config",
        action="store",
        required=True,
        help="config file (in YAML format)",
    )

    arg_parser.add_argument(
        '-o',
        "--output-path",
        action="store",
        required=True,
        help="path to folder in which to save output (will be created if necessary)",
    )

    arg_parser.add_argument(
        "--output-type",
        choices=["csv", "sqlite"],
        action="store",
        default="csv",
        help="output csv files (one per table, default) or a sqlite database",
    )

    arg_parser.add_argument(
        "--clean",
        action="store_true",
        help="If supplied, parser will empty output directory before parsing"
    )

    args = arg_parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    log_level = logging.CRITICAL if args.quiet else log_level

    logger = logging.getLogger(__name__)
    logger.setLevel(log_level)
    logger.addHandler(logging.StreamHandler())

    convertor = PatentTxtToTabular(**vars(args), logger=logger)
    convertor.convert()


if __name__ == "__main__":
    main()
