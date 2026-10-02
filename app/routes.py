from flask import render_template
from flask import request
from flask import Response
from flask import send_file, jsonify
from flask import abort
from app import app
from app import DataLoader
from app.BrowserPlotCache import (
    BUBBLE_BROWSER_FORMAT, prepare_bubble_browser_cache, prepare_plot_gzip,
)
from app.FunderData import FunderDataStore, FunderDataUnavailable
from app.DashboardFilters import (
    DashboardSelectionUnavailable,
    get_dashboard_filter_store,
    load_precomputed_facet_overview,
)
import os


PLOT_JSON_FILES = frozenset(
    filename for filename in DataLoader.TOPLOT_RUNTIME_FILES
    if filename.endswith(".json")
)
VERSIONED_CACHE_SECONDS = 31536000

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
        parentTerms = dataLoader.getTermsList()
        summary = dataLoader.getSummaryStatistics()
        plot_versions = {
            filename: str(os.stat(
                os.path.join(published_path, "toplot", filename)
            ).st_mtime_ns)
            for filename in PLOT_JSON_FILES
            if os.path.isfile(os.path.join(
                published_path, "toplot", filename
            ))
        }

        return render_template(
            'index.html', title='Home', switches='true',
            ancestries=ancestries, parentTerms=parentTerms, summary=summary,
            plot_versions=plot_versions,
            bubble_browser_format=BUBBLE_BROWSER_FORMAT,
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
        facet_summary = load_precomputed_facet_overview(published_path)
        if facet_summary is None:
            facet_summary = get_dashboard_filter_store(
                published_path
            ).facet_overview()
        return render_template(
            'pages/additional-information.html', summary=summary,
            facet_summary=facet_summary,
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
        return send_file(
            path,
            mimetype="text/csv",
            as_attachment=True,
            download_name=filename + ".csv",
            conditional=True,
        )


@app.route("/json/<filename>")
def getplotjson(filename):
    if filename not in PLOT_JSON_FILES:
        abort(404)
    with DataLoader.published_data_lock() as published_path:
        path = os.path.join(published_path, "toplot", filename)
        if not os.path.isfile(path):
            abort(404)
        source_stat = os.stat(path)
        current_version = str(source_stat.st_mtime_ns)
        version_matches = request.args.get("v") == current_version
        compressed_path = path + ".gz"
        compressed_available = os.path.isfile(compressed_path) and \
            os.stat(compressed_path).st_mtime_ns >= source_stat.st_mtime_ns
        if filename == "bubbleGraph.json" and \
                request.args.get("format") == BUBBLE_BROWSER_FORMAT:
            try:
                path, compressed_path = prepare_bubble_browser_cache(
                    published_path
                )
                compressed_available = True
            except (OSError, ValueError, KeyError, TypeError):
                # Read-only deployments may not allow a disposable cache. The
                # original representation remains fully chart-compatible.
                app.logger.warning(
                    "Could not prepare the compact browser plot cache",
                    exc_info=True,
                )
                version_matches = False
        if not compressed_available and request.accept_encodings["gzip"] > 0:
            try:
                compressed_path = prepare_plot_gzip(published_path, filename)
                compressed_available = True
            except OSError:
                # Compression is an optional accelerator, not a prerequisite
                # for serving a complete published release.
                pass
        use_compressed = compressed_available and \
            request.accept_encodings["gzip"] > 0
        response = send_file(
            compressed_path if use_compressed else path,
            mimetype="application/json",
            conditional=True,
            max_age=(VERSIONED_CACHE_SECONDS if version_matches else 0),
        )
        response.vary.add("Accept-Encoding")
        if use_compressed:
            response.headers["Content-Encoding"] = "gzip"
        if version_matches:
            response.cache_control.immutable = True
        return response


@app.route("/api/traits", methods=['GET'])
def getFilterTraits():
    search = request.args.get("search")
    if search is None:
        search = ''
    with DataLoader.published_data_lock() as published_path:
        dataLoader = DataLoader.DataLoader(published_path)
        return jsonify(results=dataLoader.filterTraits(search))


def _filter_query_values(plural_name, legacy_name):
    values = request.args.getlist(plural_name)
    values += request.args.getlist(f"{plural_name}[]")
    if not values:
        values = request.args.getlist(legacy_name)

    result = []
    seen = set()
    for value in values:
        for token in str(value or "").split(","):
            token = token.strip()
            if token and token not in seen:
                result.append(token)
                seen.add(token)
    return tuple(result)


@app.route("/api/funders", methods=["GET"])
def getFilterFunders():
    search = (
        request.args.get("search")
        or request.args.get("term")
        or ""
    ).strip()
    cohort_ids = _filter_query_values("cohorts", "dataset")
    stage = (request.args.get("stage") or "").strip()
    page = max(request.args.get("page", default=1, type=int), 1)
    page_size = 50
    complete_list = len(cohort_ids) <= 1 and stage.casefold() in {
        "initial", "discovery", "replication",
    }
    with DataLoader.published_data_lock() as published_path:
        store = get_dashboard_filter_store(published_path)
        try:
            entries = store.funders(search, cohort_ids, stage)
        except KeyError:
            abort(404)
        except ValueError:
            abort(400)
        start = 0 if complete_list else (page - 1) * page_size
        page_entries = entries if complete_list \
            else entries[start:start + page_size]
        return jsonify(results=[{
            "id": entry["slug"],
            "text": entry["name"],
            "studyCount": entry["studyCount"],
            "publicationCount": entry["publicationCount"],
        } for entry in page_entries], pagination={
            "more": False if complete_list \
            else start + page_size < len(entries),
        })


@app.route("/api/cohorts", methods=["GET"])
@app.route("/api/datasets", methods=["GET"])
def getFilterDatasets():
    search = (
        request.args.get("search")
        or request.args.get("term")
        or ""
    ).strip()
    funder_slugs = _filter_query_values("funders", "funder")
    stage = (request.args.get("stage") or "").strip()
    page = max(request.args.get("page", default=1, type=int), 1)
    page_size = 50
    complete_list = len(funder_slugs) <= 1 and stage.casefold() in {
        "initial", "discovery", "replication",
    }
    with DataLoader.published_data_lock() as published_path:
        store = get_dashboard_filter_store(published_path)
        try:
            entries = store.cohorts(search, funder_slugs, stage)
        except KeyError:
            abort(404)
        except ValueError:
            abort(400)
        start = 0 if complete_list else (page - 1) * page_size
        page_entries = entries if complete_list \
            else entries[start:start + page_size]
        return jsonify(results=[{
            "id": entry["id"],
            "text": entry["name"],
            "studyCount": entry["studyCount"],
            "publicationCount": entry["publicationCount"],
        } for entry in page_entries], pagination={
            "more": False if complete_list \
            else start + page_size < len(entries),
        })


@app.route("/json/filtered-dashboard.json")
def getFilteredDashboard():
    cohort_ids = _filter_query_values("cohorts", "dataset")
    funder_slugs = _filter_query_values("funders", "funder")
    if not cohort_ids and not funder_slugs:
        abort(400)
    with DataLoader.published_data_lock() as published_path:
        store = get_dashboard_filter_store(published_path)
        try:
            path = store.dashboard_path(cohort_ids, funder_slugs)
        except KeyError:
            abort(404)
        return send_file(path, mimetype="application/json", conditional=True)


@app.route("/download/filtered-dashboard.zip")
def getFilteredDashboardDownload():
    cohort_ids = _filter_query_values("cohorts", "dataset")
    funder_slugs = _filter_query_values("funders", "funder")
    if not cohort_ids and not funder_slugs:
        abort(400)
    with DataLoader.published_data_lock() as published_path:
        store = get_dashboard_filter_store(published_path)
        try:
            path = store.download_path(cohort_ids, funder_slugs)
        except KeyError:
            abort(404)
        selection = "-".join(cohort_ids + funder_slugs)
        if len(selection) > 100:
            selection = f"multiple-{len(cohort_ids)}-cohorts-" \
                f"{len(funder_slugs)}-funders"
        return send_file(
            path,
            as_attachment=True,
            download_name=f"gwas-selection-{selection}.zip",
        )


@app.route("/reports/filtered-dashboard")
def getFilteredDashboardReport():
    cohort_ids = _filter_query_values("cohorts", "dataset")
    funder_slugs = _filter_query_values("funders", "funder")
    if not cohort_ids and not funder_slugs:
        abort(400)
    with DataLoader.published_data_lock() as published_path:
        store = get_dashboard_filter_store(published_path)
        try:
            dashboard = store.dashboard(cohort_ids, funder_slugs)
            report = store.report(cohort_ids, funder_slugs)
        except KeyError:
            abort(404)

        selection = dashboard["selection"]
        cohorts = selection.get("cohorts") or (
            [selection["dataset"]] if selection.get("dataset") else []
        )
        funders = selection.get("funders") or (
            [selection["funder"]] if selection.get("funder") else []
        )
        cohort_names = ", ".join(entry["name"] for entry in cohorts)
        funder_names = ", ".join(entry["name"] for entry in funders)
        if cohorts and funders:
            report_title = f"{cohort_names} / {funder_names}"
            report_subtitle = "Cohort and funding-linked diversity report"
            report_note = (
                "This report reflects the intersection of the selected "
                "cohorts and funders."
            )
        elif cohorts:
            report_title = cohort_names
            report_subtitle = "Cohort diversity report"
            report_note = "This report reflects the selected cohorts."
        else:
            report_title = funder_names
            report_subtitle = "Funding-linked diversity report"
            report_note = "This report reflects the selected funders."
        download_url = request.url_root.rstrip("/") + \
            "/download/filtered-dashboard.zip?" + request.query_string.decode()
        return render_template(
            "pages/funder-report.html",
            title=f"{report_title} report",
            report_title=report_title,
            report_subtitle=report_subtitle,
            report=report,
            download_url=download_url,
            report_note=report_note,
        )


@app.route("/json/funders/<slug>.json")
def getFunderDashboard(slug):
    with DataLoader.published_data_lock() as published_path:
        store = FunderDataStore(published_path)
        try:
            path = store.dashboard_path(slug)
            store.dashboard(slug)
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
