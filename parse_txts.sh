#!/bin/bash
#SBATCH --job-name=txtprse
#SBATCH --output=txt_parse.out
#
#SBATCH --partition=hlwill
#SBATCH --time=7-00:00:00
#
#SBATCH --mem=0
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=20
#
#SBATCH --mail-type=ALL
#SBATCH --mail-user=gsmoore@stanford.edu

#Empty the temp dir
rm $SCRATCH/txt_parse_temp -r
mkdir $SCRATCH/txt_parse_temp

#Unzip the TXT files to parse
for i in /oak/stanford/groups/hlwill/raw/USPTO_grants/data/pftaps*.zip; do unzip "$i" -d $SCRATCH/txt_parse_temp & done

# Wait until all the files are unzipped before beginning parsing
wait

#Parse the files
python3 patent_txt_to_csv.py -i $SCRATCH/txt_parse_temp -o output --output-type sqlite -c config.yaml --clean -r

#Remove temp files
rm $SCRATCH/txt_parse_temp -r
