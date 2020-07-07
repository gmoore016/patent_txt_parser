class PatentTxtToTabular:
    def __init__(self, txt_input, config, output_path, output_type, logger, **kwargs,):

        self.logger = logger

        self.txt_files = []
        for input_path in xml_input:
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
                        yield (i - len(txtdoc), "".join(txtdoc))
                    txt_doc = []

                # Add line to current document
                txt_doc.append(line)

            # Make sure you yield the final document!
            yield(i - len(txtdoc), "".join(txtdoc))

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

    def process_doc(self, txt_doc):
        """The method for actually reading the contents of the CSV files"""
        # Go through each line of the file
        for line in self.text_doc.split('\n'):

            # Get the first four characters to see if we're in a new logical unit
            header = line[0:4].strip()
            if len(header) == 4:
                self.tables[current_entity].append(record)
                # Change the header and current config if so
                current_entity = config[header]['<entity>']
                subconfig = self.config[header]['<fields>']
                record = {}

            if header in subconfig:
                fieldname = subconfig[header]
                value = line[4:].strip()
                record[fieldname] = value

        self.tables[current_entity].append(record)




