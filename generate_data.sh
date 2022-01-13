#!/bin/bash

dir_path=$(dirname $(realpath $0))

# activate python virtual environment
. $dir_path/venv/bin/activate

# execute python script
python3 generate_data.py

# deactive virtual environment
deactivate