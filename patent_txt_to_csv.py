import argparse  # Takes command line arguments
import csv       # Handles CSV output
import logging   # Handles logging output
import yaml      # Takes input files
import re        # Regular expressions
import shutil    # Handles system paths

from collections import defaultdict   # Dictionaries that provide default values
from pathlib import Path              # Feature-rich path objects
from pprint import pformat            # Prints data in a nice way

try:
    from termcolor import colored         # Allows colored terminal output
except ImportError:
    logging.debug("termcolor not available")

    def colored(text, _color):
        """
        Dummy function in case termcolor is not available
        """
        return text


class PatentTxtToTabular:
    def __init__(self, txt_input, config, output_path, output_type, logger, clean, **kwargs,):

        self.logger = logger

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

    def init_cache_vars(self):
        self.tables = defaultdict(list)
        self.table_pk_idx = defaultdict(lambda: defaultdict(int))

    def yield_txt_doc(self, filepath):
        # List for storing text
        txt_doc = []

        # Opens lines from file
        with open(filepath, "r") as _fh:

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
        if not self.txt_files:
            self.logger.warning(colored("No input files to prcoess!", "red",))

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

            self.flush_to_disk()

    def flush_to_disk(self):
        if self.output_type == "csv":
            self.write_csv_files()
        if self.output_type == "sqlite":
            self.write_sqlitedb()

        self.init_cache_vars()

    def get_fieldnames(self):
        fieldnames = defaultdict(list)

        def add_fieldnames(config, _fieldnames, parent_entity=None):
            if isinstance(config, str):
                if ":" in config:
                    _fieldnames.append(config.split(":")[0])
                    return
                _fieldnames.append(config)
                return

            if "<fieldname>" in config:
                _fieldnames.append(config["<fieldname>"])
                return

            # If there's a new entity, we save the ids and recursively
            # fetch the fieldnames in the substructure
            if "<entity>" in config:
                entity = config["<entity>"]
                _fieldnames = []
                if "<primary_key>" in config or parent_entity:
                    _fieldnames.append("id")
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

        return fieldnames

    def process_doc(self, txt_doc):
        """The method for actually reading the contents of the CSV files"""
        # Initialize with PATN since we know first section of document will
        # be a patent.
        header = "PATN"
        current_entity = self.config[header]['<entity>']
        subconfig = self.config[header]['<fields>']
        record = {}

        # Go through each line of the file
        # Need to skip first two lines since they contain metadata
        # and we don't want to write an empty patent record
        for line in txt_doc.split('\n')[1:]:

            # Get the first four characters to see if we're in a new logical unit
            header = line[0:4].strip()
            if len(header) == 4:
                # Change the header and current config if so
                # If we care about the new section, write what we've found to a file
                # and fetch the new subsections
                if header in self.config:
                    self.tables[current_entity].append(record)
                    current_entity = self.config[header]['<entity>']
                    subconfig = self.config[header]['<fields>']
                    record = {}

                # If we don't care about the new section, just say it has no relevant
                # fields and continue. Don't create a new record or write yet, since that
                # will create empty lines for the null section
                else:
                    subconfig = {}

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
                        record[fieldname] = value

                    # If the value is parameterized, we need to handle
                    # many-to-one issues
                    elif "<fieldname>" in subconfig[entry]:
                        # First, save the fieldname
                        fieldname = subconfig[entry]["<fieldname>"]

                        # If we've seen one before, add the new one with a delimiter
                        if fieldname in record:
                            record[fieldname] = record[fieldname] \
                                                + subconfig[entry]["<joiner>"] \
                                                + value
                        # Otherwise, just save the value for now
                        else:
                            record[fieldname] = value

        # Add to list of those entities found so far
        self.tables[current_entity].append(record)

    def write_csv_files(self):

        self.logger.info(
            colored("writing csv files to %s ...", "green"), self.output_path.resolve()
        )

        for tablename, rows in self.tables.items():
            output_file = self.output_path / f"{tablename}.csv"

            if output_file.exists():
                self.logger.debug(
                    colored("CSV file %s exists; records will be appended.", "yellow"),
                    output_file
                )

                with output_file.open("a", newline='') as _fh:
                    writer = csv.DictWriter(_fh, fieldnames=self.fieldnames[tablename])
                    writer.writerows(rows)

            else:
                with output_file.open("w", newline='') as _fh:
                    writer = csv.DictWriter(_fh, fieldnames=self.fieldnames[tablename])
                    writer.writeheader()
                    writer.writerows(rows)


def expand_paths(path_expr):
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
