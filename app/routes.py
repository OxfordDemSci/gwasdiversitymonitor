from flask import render_template
from flask import request
from flask import Response
from flask import send_file, jsonify
from flask import abort
from app import app
from app import DataLoader
import os

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
    with DataLoader.published_data_lock() as published_path:
        dataLoader = DataLoader.DataLoader(published_path)

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

        return render_template(
            'index.html', title='Home', switches='true',
            ancestries=ancestries, ancestriesOrdered=ancestriesOrdered,
            parentTerms=parentTerms, traits=traits, summary=summary,
            bubbleGraph=bubbleGraph, tsPlot=tsPlot, chloroMap=chloroMap,
            heatMap=heatMap, doughnutGraph=doughnutGraph
        )

@app.route('/privacy-policy')
def privacy():
    return render_template('pages/privacy-policy.html', title='Privacy Policy', alwaysShowCookies=1)

@app.route('/qandas')
def qandas():
    return render_template('pages/qandas.html', title='Q&As')

@app.route('/additional-information')
def additional():
    with DataLoader.published_data_lock() as published_path:
        dataLoader = DataLoader.DataLoader(published_path)
        summary = dataLoader.getSummaryStatistics()
        return render_template(
            'pages/additional-information.html', summary=summary,
            title='Additional Information'
        )

@app.route("/getCSV/<filename>")
def getCSV(filename):

    zip_downloads = {"heatmap", "timeseries", "gwasdiversitymonitor_download"}
    csv_downloads = {"bubble_df", "choro_df", "doughnut_df"}
    with DataLoader.published_data_lock() as published_path:
        if filename in zip_downloads:
            path = os.path.join(
                published_path, 'todownload', filename + '.zip'
            )
            if not os.path.exists(path):
                abort(404)
            return send_file(
                path, as_attachment=True, download_name=filename + '.zip'
            )

        if filename not in csv_downloads:
            abort(404)

        path = os.path.join(published_path, 'toplot', filename + '.csv')
        if not os.path.exists(path):
            abort(404)

        with open(path) as fp:
            csv = fp.read()

        return Response(
            csv,
            mimetype="text/csv",
            headers={"Content-disposition":
                     "attachment; filename="+filename+".csv"})


@app.route("/json/<filename>")
def getplotjson(filename):
    with DataLoader.published_data_lock() as published_path:
        with open(os.path.join(published_path, 'toplot', filename)) as fp:
            json = fp.read()

            return Response(
                json,
                mimetype="application/json")


@app.route("/api/traits", methods=['GET'])
def getFilterTraits():
    search = request.args.get("search")
    if search is None:
        search = ''
    with DataLoader.published_data_lock() as published_path:
        dataLoader = DataLoader.DataLoader(published_path)
        return jsonify(results=dataLoader.filterTraits(search))


@app.errorhandler(DataLoader.PublishedDataUnavailable)
def published_data_unavailable(error):
    return Response(str(error), status=503, mimetype='text/plain')
