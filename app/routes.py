import json

from flask import render_template
from flask import request
from flask import Response
from flask import send_file, jsonify
from app import app
from app import DataLoader

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
        return send_file('data/todownload/'+filename+'.zip')

    with open('data/toplot/'+filename+'.csv') as fp:
        csv = fp.read()

    return Response(
        csv,
        mimetype="text/csv",
        headers={"Content-disposition":
                 "attachment; filename="+filename+".csv"})


@app.route("/json/<filename>")
def getplotjson(filename):
    with open(f'data/toplot/{filename}') as fp:
        json = fp.read()

        return Response(
            json,
            mimetype="application/json")


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
    with open("funders_cleaner.json") as file:
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
            funders_list.append({"id": group_index, "text": f"{grouping}", "inc": []})
            # Current group will be the last element of the list as it was just appended
            current_group = funders_list[len(funders_list)-1]
            for funder_index, funder in enumerate(sorted_group_dict[grouping], start=1):
                # "id" is used to determine tree hierachy in funder dropdown
                element_tree_id = (group_index * 10) + funder_index
                current_group["inc"].append({"id": element_tree_id, "text": f"{funder}"})

    # Store generated file to access for future retrieval
    # json_object = json.dumps({"data": funders_list})
    # with open("funders_cleaner_formatted.json", "w") as file:
    #     file.write(json_object)

    return ({"data": funders_list})
