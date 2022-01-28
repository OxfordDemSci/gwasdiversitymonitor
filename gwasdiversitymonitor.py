from app import app
from app import generate_data
import os
import sys

if __name__ == '__main__':
    required_data_file = 'app/data/toplot/chloroMap.json'
    if not os.path.exists(required_data_file):
        sys.stderr.write('Missing required data files. Attempting to download them now\n')
        generate_data.main()
    if not os.path.exists(required_data_file):
        sys.stderr.write('Data download failed. Aborting\n')
        sys.exit(1)
    app.run(host='0.0.0.0', debug=True)
