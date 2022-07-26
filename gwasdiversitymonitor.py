from app import app
from app import generate_data
import os
import sys

if __name__ == '__main__':
    required_data_file = 'app/data/toplot/chloroMap.json'
    if not os.path.exists(required_data_file):
        sys.stderr.write('Missing required data files. Please run python3 ./app/generate_data.py!\n')
        sys.exit(1)
    app.run(host='0.0.0.0', debug=True)
