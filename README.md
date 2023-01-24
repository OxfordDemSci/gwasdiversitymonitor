<p align="left">
  <img src="https://github.com/OxfordDemSci/gwasdiversitymonitor/blob/master/app/static/images/logo_white_rect.png" width="450"/>
</p>

[![DOI](https://zenodo.org/badge/220447592.svg)](https://zenodo.org/badge/latestdoi/220447592) [![Generic badge](https://img.shields.io/badge/Python-3.6-<red>.svg)](https://shields.io/)  [![Generic badge](https://img.shields.io/badge/License-MIT-green.svg)](https://shields.io/)  [![Generic badge](https://img.shields.io/badge/Maintained-Yes-red.svg)](https://shields.io/)

### Introduction

This is a repository to accompany the GWAS Diversity Monitor, currently maintained as part of the [Leverhulme Centre for Demographic Science](http://www.demographicscience.ox.ac.uk/). The dashboard can be found at:

<div align="center"> <a href="http://www.gwasdiversitymonitor.com">gwasdiversitymonitor.com</a></div>
<br/>

The frontend is written in [D3 4.0](https://devdocs.io/d3~4/) (a JavaScript library for visualizing data with HTML, SVG, and CSS) with the backend hosted in [Flask](https://github.com/pallets/flask) (a lightweight WSGI web application framework written in Python). Grateful attributions regarding data are made to the [**EMBL-EBI GWAS Catalog**](https://www.ebi.ac.uk/gwas/). In summary, the dashboard visualizes a systematic interactive review of all GWAS published to date. This repo can be cloned and ran on-the-fly to generate a server on localhost as required. The dashboard and associated summary statistics check daily for updates from the Catalog and update with new releases. The cron adapts a couple of functions from our [Scientometric Review of all GWAS](https://www.nature.com/articles/s42003-018-0261-x)

### Prerequisites

As a pre-requisite to running this locally, you will need a working Python 3 installation with all the necessary dependencies detailed in [requirements.txt](https://github.com/crahal/GWASDiversityMonitor/blob/master/requirements.txt) (generated by pipreqs). We strongly recommend virtual environments and [Anaconda](https://www.anaconda.com/distribution/). Version 2.0 of the application ('By Funder') also generates reports using the [pylatex](https://jeltef.github.io/PyLaTeX/current/) module. For the `generate_data.py` script to successful call the `generate_reports.py` module, you'll need an active LaTeX installation. For that, we recommend ['TeX Live'](https://tug.org/texlive/).

### Running the Code

This server is operating system independent (through the ``os`` module) and should work on Windows, Linux and macOS all the same. To run: clone this directory, ``cd`` into the directory, download the data with "python app/generate_data.py", and serve the project by simply calling the gwasdiversitymonitor.py file. "python gwasdiversitymonitor.py". This will serve the project to port 5000 on your localhost (via Flask).

To do this run "python -m venv virtualenv" from the root of the project. This will create a directory called "virtualenv". Navigate into virtualenv/bin and run "pip install -r requirements.txt" to install the requirements of the project inside your new virtual environment. Then run the project from the root of the project (above the virtualenv/) with `./virtualenv/bin/python gwasdiversitymonitor.py`.

### Structure

The root of the folder contains routes.py, which is the main file which runs the application. This file registers paths inside the application, creates appropriate variables, and passes them into templates (html files) which renders the page. This is our Controller in an MVC framework.

Each call to @app.route('path') defines a path and almost all of these just return templates for pages. The exception is @app.route("/getCSV/<filename>") which is handling the data download for all cases and returns a file response.
The other exception in this file is, at the top, @app.context_processor. This injects some functionality into our flask templates for us. It is used to give us a user_agent checker (check what device/browser is accessing the application) check the state of the cookie policy, and inject the google analytics key from the config file.

The other important file here is DataLoader.py. This is a simple file containing a series of helper functions that route.py uses to load and reshape the data from the wrangled csv's into a shape that is workable with d3.

_Data_: pulls, wrangles and creates all data used in this project.

_Static_: This subfolder contains all of the assets for the application. CSS/Sass, fonts, images and js will all be found here. The one we care most about is the js. There is a script.js file which contains some global functions and then a file for each graph.

Each graph js file contains a function, which is then called from the global template, to instantiate the graph. The global filters recall these functions to redraw the graphs with the new filter state. "internal" filters, eg. year and parent term for each tile, are handled by event handlers inside each graph file.

### Docker Deployment

To launch the app using the Docker deployment, you must first install [Docker Compose](https://docs.docker.com/compose/install/).

The Docker deployment for this application uses three containers that are defined in `docker-compose.yml`:  
1. **gwas_nginx:** An nginx web server
2. **gwas_flask:** The Flask web application running behind a gunicorn WSGI server (see `./deploy/flask.Dockerfile`)
3. **gwas_cron:** A cron scheduler to generate new data daily (see `./deploy/cron.Dockerfile`)

To launch the Docker containers from the command line: 
```angular2html
cd .../gwasdiversitymonitor
docker-compose up -d --build
```

You can check the status of active docker containers using:
```angular2html
docker ps
docker stats
```

While the containers are running, you can view the app from your web browser by navigating to [http://localhost:80](http://localhost:80).

To stop the containers, use:
```angular2html
docker-compose down
```

### Versioning

This dashboard is currently at Version 1.0.0 (with an article conditionally accepted Nature Genetics). Please note: although the library logs data updates, it could be that additional dictionary based classifications are required with regards to the ```/data/support/dict_replacer_broad.tsv``` file. Please raise an issue in this repo to alert us of any necessary entries, or any suggestions which you may have in general, although we will monitor this over time.

### License

This work is free. You can redistribute it and/or modify it under the terms of the MIT license, subject to the conditions regarding the data imposed by the [EMBL-EBI license](https://www.ebi.ac.uk/about/terms-of-use). The dashboard comes without any warranty, to the extent permitted by applicable law.

### Acknowledgements

We are grateful to the extensive help provided by [Global Initiative](https://www.global-initiative.com/) (and in particular to Jamie, Nynke, Veatriki, Alex, Lea and Quentin). Additional help and specific comments came from [Ian Knowles](https://github.com/ianknowles), [Yi Liu](https://github.com/YiLiu6240), [Jiani Yan](https://github.com/vallerrr), [Molly Przeworski](https://przeworskilab.com/), [Ben Domingue](https://github.com/ben-domingue), [Sam Trejo](https://cepa.stanford.edu/people/sam-trejo) and the [Sociogenome](http://www.sociogenome.org) group more generally.

### Future Versions

1. Additional tabs regarding funders, network analysis, and so forth.
