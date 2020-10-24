# Patent TXT Parser

## Automated Patent System (APS) Files

Prior to 2001, the USPTO released patent data online in the TXT-based "Automated Patent System" (APS) format. Each element of an APS file begins with either a three- or four-letter code designating the contents of the line. Four letter codes delimit sections, while three letter codes denote a particular piece of data. For example, an `INVT` line denotes the beginning of the information about a given inventor. If the subsequent line were `NAM John Smith`, we would know the name of the given inventor was John Smith. 

The meanings of each section are defined in the [Patent Full Text/APS Documentation](https://bulkdata.uspto.gov/data/patent/grant/redbook/fulltext/2001/PatentFullTextAPSDoc_GreenBook.pdf) from the USPTO bulk data repository. We keep a copy of this documentation saved [in this repository](https://github.com/liegroup-stanford/patent_txt_parser/blob/master/APS_Documentation.pdf). 

## Running the Parser

This script requires at least Python 3.6 to be operational; inclusion of f-strings means older versions will not be supported.

To run the parser, run `python3 patent_txt_to_csv.py` with the following required arguments:
* `--txt-input`, `-i`: TXT file or directory of TXT files to parse recursively. Multiple arguments can be passed.
* `--output-path`, `-o`: Path to folder in which to store output (will be created if necessary).
* `--config`, `-c`: Configuration file in YAML format specifying what fields to pull and where to store them.

It also supports the following optional arguments:
* `--verbose`, `-v`: Increase verbosity
* `--quiet`, `-q`: Run with no printed output
* `--recurse`, `-r`: Recursively search input directory for TXT files to parse
* `--output-type`: Can be either `csv` or `sqlite`. Default is `csv`. Determines format of output database.
* `--clean`: Erases output directory before running when passed such that database begins from nothing. 

Thus, a standard run of the parser would look something like `python3 patent_txt_to_csv.py --txt-input $APS_DIRECTORY --output-path $OUTPUT_DIRECTORY --config $CONFIG_FILE --clean`. 

## Configuration File Format

### Base Structure

The parser takes configuration files in [YAML](https://yaml.org/) format. YAML is the 21st-century cousin of JSON, ideally being more human-readable and less prone to syntax errors. The current config file is available at [config.yaml](https://github.com/liegroup-stanford/patent_txt_parser/blob/master/config.yaml).

In general, the top level of the YAML file will list the APS four-letter sections of interest. Each four-letter section will correspond to a table in the database, designated by the `<entity>` field. For example, information contained in the `PATN` is saved to the `patent` table, while `UREF`, `FREF`, and `OREF`--all types of citations--are saved to the `citation` table. The fields of interest are then listed under the `<fields>` field. Note that some basic regular expressions are supported here; for example `PA[A-Z1-9]` will match `PA` followed by any capital letter or number.  

As an example, the configuration file for pulling inventors might look something like this:
```yaml
INVT:
  <entity>: inventor
  <fields>:
    NAM: name
    CTY: city
    STA: state
    CNT: country
```
This would save the name, city, state, and country for each `INVT` field to their own columns in the `inventor` table. 

### Primary Keys

Your `PATN` field should contain a `<primary_key>` field designating a unique ID number for the patent; we recommend `WKU`, the patent number. These numbers must have a 1-to-1 relationship with the patents--each patent has only one number, and each number corresponds to only one patent. This "key" will be copied to child tables so it is possible to link across tables in the database.

Each secondary entity within the database will contain both `id` and `parent_id` fields. `id` will represent the parent patent of that entry, while `parent_id` will be a unique identifier within the table composed of the parent id and the entry's position in the patent's APS file. For example, if `patent` contains two records with `id`s `pat1` and `pat2`, the `citation` table may contain several records with `parent_id` `pat1` and `pat2`. However, all `id` fields within `citation` will be unique--e.g. you may have `pat1_1`, `pat2_1`, and `pat2_2`.

### Joiners

Frequently, a single patent will have multiple distinct entries of the same type. For example, a patent may have dozens of lines of claim text. Depending on their importance, we have a couple ways of handling these.

First, a field can specify a "joiner." In the case of a joiner, successive fields of the same type will be concatonated into a single string, delimited with a joiner. Note when specifying a field with parameters, you must also specify `<fieldname>` explicitly. As an example, the APS file:
```
CLMS
NUM 1
PAR foo
PAR bar
NUM 2
PAR ray
```
along with the config file
```yaml
CLMS:
  <entity>: claim
  <fields>:
    PAR:
      <fieldname>: claim_text
      <joiner>: "|#|"
```
would generate the line `foo|#|bar|#|ray` in the `claim` table under the header `claim_text`. 

### Splitters

Sometimes, the successive fields of an entry are important enough they deserve their own table. For example, we may desire each claim have its own entry in the `claim` table. In this case, so long as there exists some other element to delimit where one ends and the next begins, we can use this element in the `<splitter>` field to separate them.

Consider the APS file from above:
```
CLMS
NUM 1
PAR foo
PAR bar
NUM 2
PAR ray
```

In this case, claim 1 is composed of `foo` and `bar`, while claim 2 is composed of `ray`. Notice that each claim is delimited by a `NUM` element. In this case, we can use `NUM` as a splitter to define the claims separately. That means this config file:
```yaml
CLMS:
  <entity>: claim
  <fields>:
    PAR:
      <fieldname>: claim_text
      <joiner>: "|#|"
      <splitter>: "NUM"
```
will split the above file into two rows, `foo|#|bar` and `ray`, as desired. 

## Check Digits

For reasons that are unclear to us, the USPTO felt as though each patent and application number in their TXT data required a "check digit." The USPTO documentation for this is as follows:
```
Check Digit Modulus 11

A check digit is established for each patent number and 
application serial number that appears on the full-text file. 
The patent number check digit will appear in the 9th position of 
each record following the patent number. The check digit for the 
application serial number will appear in the 8th position of the 
Selected Front Page Information element. The check digit is 
derived in the following manner:

Multiply the right most numeric position of the field 
by 2, the next numeric position to the left by 3, the 
next by 4, the next by 5, etc. 

The products are added and divided by 11. The resulting 
remainder is subtracted from 11, which produces the check 
digit. The exception will be if the result is 11, the 
check digit will be 0, if the result is 10, the check 
digit will appear as an ampersand (&).
```

What is the purpose of this check digit? Unclear--the above is the complete discussion of the check digit in the documentation. The USPTO itself seems somewhat unsure of its logic, as they drop the check digits once they shift over to XML.

Unfortunately, the patent number is never present without the check digit, so if we're pulling the raw data from the APS files, we have no choice but to pull the check digit. However, in the interest of keeping the data "pure," we don't strip the check digit ourselves. 

What does this mean for using the data? For any patents 1976-2001, the first step of any analysis should probably be dropping the last character of all the patent and application numbers. 

## Entries to Ignore

As nice as it would be if our data were perfect, that's unfortunately not the case. Sometimes there are errors in the USPTO data which we need to clear out of our build, whether they're duplicates, malformed entries, or otherwise. To do so, we create a dictionary `ENTRIES_TO_IGNORE` at the top of our script which links filenames to a list of patent numbers which we want to ignore in our build. It is suggested you comment why a patent is being stripped. 

For example, if the dictionary looked like this:
```python
ENTRIES_TO_IGNORE = {
  "pftaps19871110_wk45.txt": [
    "H00003670", # Duplicate from November 3, 1987
    "H00003689", # Malformed duplicate from November 3, 1987
  ]
}
```
the parser would ignore patent `H00003670` when found in file `pftaps19871110_wk45.txt`. This allows us to surgically strip out only the problematic patents while leaving the rest of the data unaffected.
