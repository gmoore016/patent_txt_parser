#!/bin/bash
#SBATCH --job-name=txtprse
#SBATCH --output=txt_parse.out
#
#SBATCH --partition=hlwill
#SBATCH --time=3-00:00:00
#
#SBATCH --mem=50G
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=20
#
#SBATCH --mail-type=ALL
#SBATCH --mail-user=gsmoore@stanford.edu

#Empty the temp dir
rm $SCRATCH/txt_parse_temp -r
mkdir $SCRATCH/txt_parse_temp

#Unzip the TXT files to parse
unzip /oak/stanford/groups/hlwill/USPTO_grants/data/pftaps*.zip -d $SCRATCH/txt_parse_temp

#Parse the files
python3 patent_txt_to_csv.py -i $SCRATCH/txt_parse_temp -o output -c config.yaml --clean

#Remove temp files
rm $SCRATCH/txt_parse_temp -r
