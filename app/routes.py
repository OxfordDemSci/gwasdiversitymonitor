from flask import render_template
from flask import request
from flask import Response
from flask import send_file, jsonify
from flask import abort
from app import app
from app import DataLoader
from app.FunderData import FunderDataStore, FunderDataUnavailable
from app.DashboardFilters import (
    DashboardSelectionUnavailable,
    get_dashboard_filter_store,
)
import os

@app.context_processor
def inject_template_scope():
    injections = dict()

    browser = request.user_agent.browser
    injections.update(browser=browser)

    injections.update(
        goatcounter_url=app.config.get("GOATCOUNTER_URL", "").rstrip("/")
    )

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
    return render_template('pages/privacy-policy.html', title='Privacy Policy')

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


@app.route("/api/funders", methods=["GET"])
def getFilterFunders():
    search = (
        request.args.get("search")
        or request.args.get("term")
        or ""
    ).strip().casefold()
    dataset_id = (request.args.get("dataset") or "").strip()
    with DataLoader.published_data_lock() as published_path:
        store = FunderDataStore(published_path)
        allowed_funders = None
        if dataset_id:
            try:
                allowed_funders = get_dashboard_filter_store(
                    published_path
                ).funders_for_dataset(dataset_id)
            except KeyError:
                abort(404)
        return jsonify(results=[
            {
                "id": entry["slug"],
                "text": entry["name"],
                "studyCount": entry["studyCount"],
            }
            for entry in store.entries()
            if (allowed_funders is None or entry["slug"] in allowed_funders)
            and (not search or search in entry["name"].casefold()
                 or search in entry["slug"].casefold())
        ])


@app.route("/api/datasets", methods=["GET"])
def getFilterDatasets():
    search = (
        request.args.get("search")
        or request.args.get("term")
        or ""
    ).strip()
    funder_slug = (request.args.get("funder") or "").strip() or None
    page = max(request.args.get("page", default=1, type=int), 1)
    page_size = 50
    with DataLoader.published_data_lock() as published_path:
        store = get_dashboard_filter_store(published_path)
        try:
            entries = store.datasets(search, funder_slug)
        except KeyError:
            abort(404)
        start = (page - 1) * page_size
        page_entries = entries[start:start + page_size]
        return jsonify(results=[{
            "id": entry["id"],
            "text": entry["name"],
            "studyCount": entry["studyCount"],
        } for entry in page_entries], pagination={
            "more": start + page_size < len(entries),
        })


@app.route("/json/filtered-dashboard.json")
def getFilteredDashboard():
    dataset_id = (request.args.get("dataset") or "").strip()
    funder_slug = (request.args.get("funder") or "").strip() or None
    if not dataset_id:
        abort(400)
    with DataLoader.published_data_lock() as published_path:
        store = get_dashboard_filter_store(published_path)
        try:
            path = store.dashboard_path(dataset_id, funder_slug)
        except KeyError:
            abort(404)
        return send_file(path, mimetype="application/json", conditional=True)


@app.route("/download/filtered-dashboard.zip")
def getFilteredDashboardDownload():
    dataset_id = (request.args.get("dataset") or "").strip()
    funder_slug = (request.args.get("funder") or "").strip() or None
    if not dataset_id:
        abort(400)
    with DataLoader.published_data_lock() as published_path:
        store = get_dashboard_filter_store(published_path)
        try:
            dataset = store.dataset(dataset_id)
            path = store.download_path(dataset_id, funder_slug)
        except KeyError:
            abort(404)
        selection = dataset_id
        if funder_slug:
            selection = f"{selection}-{funder_slug}"
        return send_file(
            path,
            as_attachment=True,
            download_name=f"gwas-selection-{selection}.zip",
        )


@app.route("/reports/filtered-dashboard")
def getFilteredDashboardReport():
    dataset_id = (request.args.get("dataset") or "").strip()
    funder_slug = (request.args.get("funder") or "").strip() or None
    if not dataset_id:
        abort(400)
    with DataLoader.published_data_lock() as published_path:
        store = get_dashboard_filter_store(published_path)
        try:
            dashboard = store.dashboard(dataset_id, funder_slug)
        except KeyError:
            abort(404)

        selection = dashboard["selection"]
        dataset = selection["dataset"]
        funder = selection["funder"]
        report_title = dataset["name"]
        report_subtitle = "Dataset diversity report"
        if funder:
            report_title = f"{dataset['name']} / {funder['name']}"
            report_subtitle = "Dataset and funding-linked diversity report"
        download_url = request.url_root.rstrip("/") + \
            "/download/filtered-dashboard.zip?" + request.query_string.decode()
        return render_template(
            "pages/funder-report.html",
            title=f"{report_title} report",
            report_title=report_title,
            report_subtitle=report_subtitle,
            report=dashboard["report"],
            download_url=download_url,
            report_note=(
                "This report reflects the selected dataset"
                + (" and funder intersection." if funder else ".")
            ),
        )


@app.route("/json/funders/<slug>.json")
def getFunderDashboard(slug):
    with DataLoader.published_data_lock() as published_path:
        store = FunderDataStore(published_path)
        try:
            path = store.dashboard_path(slug)
        except KeyError:
            abort(404)
        if not os.path.isfile(path):
            abort(404)
        return send_file(path, mimetype="application/json", conditional=True)


@app.route("/download/funders/<slug>.zip")
def getFunderDownload(slug):
    with DataLoader.published_data_lock() as published_path:
        store = FunderDataStore(published_path)
        try:
            entry = store.entry(slug)
            path = store.download_path(slug)
        except KeyError:
            abort(404)
        if not os.path.isfile(path):
            abort(404)
        return send_file(
            path,
            as_attachment=True,
            download_name=f"gwas-funder-{slug}.zip",
        )


@app.route("/reports/funders/<slug>")
def getFunderReport(slug):
    with DataLoader.published_data_lock() as published_path:
        store = FunderDataStore(published_path)
        try:
            dashboard = store.dashboard(slug)
        except KeyError:
            abort(404)
        return render_template(
            "pages/funder-report.html",
            title=f"{dashboard['funder']['name']} report",
            report_title=dashboard["funder"]["name"],
            report_subtitle="Funding-linked diversity report",
            report=dashboard["report"],
            download_url=request.url_root.rstrip("/")
            + f"/download/funders/{slug}.zip",
            report_note=(
                "Funding links are derived from PubMed grant metadata and "
                "may not capture every source of support acknowledged by "
                "each publication."
            ),
        )


@app.errorhandler(DataLoader.PublishedDataUnavailable)
def published_data_unavailable(error):
    return Response(str(error), status=503, mimetype='text/plain')


@app.errorhandler(FunderDataUnavailable)
def funder_data_unavailable(error):
    return Response(str(error), status=503, mimetype="text/plain")


@app.errorhandler(DashboardSelectionUnavailable)
def dashboard_selection_unavailable(error):
    return jsonify(error=str(error)), 422
