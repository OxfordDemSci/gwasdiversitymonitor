import os
import json
import gzip

from flask_caching import Cache

from flask import (
    request,
    render_template,
    Response,
    send_file,
    jsonify
)

from app import app, DataLoader


cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache'})


@app.context_processor
def inject_template_scope():
    injections = dict()

    browser = request.user_agent.browser
    injections.update(browser=browser)

    def cookies_check():
        value = request.cookies.get('cookie_consent')
        return value == 'true'
    injections.update(cookies_check=cookies_check)

    if "GA_KEY" in app.config :
        injections.update(key=app.config["GA_KEY"])

    return injections


@app.route('/')
@app.route('/index')
def index():

    dataLoader = DataLoader.DataLoader()

    ancestries = dataLoader.getAncestriesList()
    ancestriesOrdered = dataLoader.getAncestriesListOrder()
    parentTerms = dataLoader.getTermsList()
    traits = dataLoader.getTraitsList()
    summary = dataLoader.getSummaryStatistics()
    bubbleGraph = dataLoader.getBubbleGraph()
    tsPlot = dataLoader.getTSPlot()
    chloroMap = dataLoader.getChloroMap()
    heatMap = dataLoader.getHeatMap()
    doughnutGraph = dataLoader.getDoughnutGraph(ancestriesOrdered)

    # Works with the full integration endpoint
    filename_list = ["tsPlot.json", "heatMap.json", "ancestriesOrdered.json", "chloroMap.json", "doughnutGraph.json", "bubbleGraph.json"]

    for filename in filename_list:
        if cache.get(filename) is None:
            response = getplotjson(filename=filename)
            continue

    return render_template('index.html', title='Home', switches='true', ancestries=ancestries, ancestriesOrdered=ancestriesOrdered, parentTerms=parentTerms, traits=traits, summary=summary, bubbleGraph=bubbleGraph, tsPlot=tsPlot, chloroMap=chloroMap, heatMap=heatMap, doughnutGraph=doughnutGraph)


@app.route('/privacy-policy')
def privacy():
    return render_template('pages/privacy-policy.html', title='Privacy Policy', alwaysShowCookies=1)


@app.route('/qandas')
def qandas():
    return render_template('pages/qandas.html', title='Q&As')


@app.route('/additional-information')
def additional():
    dataLoader = DataLoader.DataLoader()
    summary = dataLoader.getSummaryStatistics()
    return render_template('pages/additional-information.html', summary=summary, title='Additional Information')


@app.route("/getCSV/<filename>")
def getCSV(filename):

    if filename == "heatmap" or filename == "timeseries" or filename == "gwasdiversitymonitor_download":
        return send_file(os.path.join('..', 'data', 'todownload', filename + '.zip'))

    with open(os.path.join('..', 'data', 'toplot', filename + '.csv')) as fp:
        csv = fp.read()

    return Response(
        csv,
        mimetype="text/csv",
        headers={"Content-disposition":
                 "attachment; filename="+filename+".csv"})


@app.route("/json/<filename>")
@cache.cached(timeout=3000)
def getplotjson(filename):
    # Check if JSON is already in cache even when initialising
    compressed_json_data = cache.get(filename)
    if compressed_json_data is None:
        with open(f'data/toplot/{filename}') as fp:
            json_data = fp.read()
            compressed_json_data = gzip.compress(json_data.encode('utf-8'))
            cache.set(filename, compressed_json_data)
            compressed_json_data = cache.get(filename)

    response = Response(compressed_json_data, mimetype="application/json")
    response.headers['Content-Encoding'] = 'gzip'

    return response


@app.route("/api/traits", methods=['GET'])
def getFilterTraits():
    search = request.args.get("search")
    if search is None:
        search = ''
    dataLoader = DataLoader.DataLoader()
    return jsonify(results=dataLoader.filterTraits(search))


@app.route("/api/funders")
def getFilterFunders():
    funders_list = []
    # Converted the funders_cleaner.txt to json for easier traversal
    with open("data/support/funder_cleaner.json") as file:
        data = json.load(file)
        # Sorting the data in the backend before sending returning response
        sorted_grouping_list = sorted([grouping for grouping in set(data.values())])
        grouping_dict = {grouping: [] for grouping in sorted_grouping_list}

        for funder, grouping in data.items():
            grouping_dict[grouping].append(funder)

        sorted_group_dict = {}
        for grouping in grouping_dict.keys():
            sorted_group_dict[grouping] = sorted(grouping_dict[grouping])

        for group_index, grouping in enumerate(sorted_group_dict.keys(), start=1):
            text = grouping
            # Avoids duplicate element ids
            if group_index < 11:
                current_group = {"id": group_index, "text": text}
            else:
                current_group = {"id": (group_index * 10), "text": text}
            current_group["inc"] = []
            funders_list.append(current_group)
            # Current group will be the last element of the list as it was just appended
            for funder_index, funder in enumerate(sorted_group_dict[grouping], start=1):
                # "id" is used to determine tree hierachy in funder dropdown
                if group_index < 11:
                    element_tree_id = (group_index * 10) + funder_index
                else:
                    element_tree_id = (group_index * 100) + funder_index
                current_group["inc"].append({"id": element_tree_id, "text": f"{funder}"})

    json_object = json.dumps({"data": funders_list}, sort_keys=False)
    return (json_object)
