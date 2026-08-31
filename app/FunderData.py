import json
import os

import funder_pipeline


class FunderDataUnavailable(RuntimeError):
    pass


class FunderDataStore:
    def __init__(self, data_path="data"):
        self.root = os.path.abspath(os.path.join(data_path, "funders"))
        self._index = None

    def _load_json(self, path):
        with open(path, encoding="utf-8") as source:
            return json.load(source)

    def index(self):
        if self._index is None:
            path = os.path.join(self.root, "index.json")
            try:
                payload = self._load_json(path)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                raise FunderDataUnavailable(
                    "Funder data have not been generated"
                ) from error

            if not isinstance(payload, dict):
                raise FunderDataUnavailable("The funder index is invalid")
            funders = payload.get("funders")
            if payload.get("version") != funder_pipeline.ARTIFACT_VERSION:
                raise FunderDataUnavailable(
                    "Funder data use an obsolete schema and must be regenerated"
                )
            if not isinstance(funders, list):
                raise FunderDataUnavailable("The funder index is invalid")
            self._index = payload
        return self._index

    def entries(self):
        return self.index()["funders"]

    def entry(self, slug):
        for entry in self.entries():
            if entry.get("slug") == slug:
                return entry
        raise KeyError(slug)

    def dashboard_path(self, slug):
        self.entry(slug)
        return os.path.join(self.root, "dashboards", f"{slug}.json")

    def download_path(self, slug):
        self.entry(slug)
        return os.path.join(self.root, "downloads", f"{slug}.zip")

    def dashboard(self, slug):
        try:
            payload = self._load_json(self.dashboard_path(slug))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise FunderDataUnavailable(
                f"Dashboard data are unavailable for {slug}"
            ) from error
        if not isinstance(payload, dict):
            raise FunderDataUnavailable(
                f"Dashboard data are invalid for {slug}"
            )
        if payload.get("version") != funder_pipeline.ARTIFACT_VERSION:
            raise FunderDataUnavailable(
                f"Dashboard data use an obsolete schema for {slug}"
            )
        try:
            funder_pipeline.validate_report(
                payload.get("report"), require_content=True
            )
        except ValueError as error:
            raise FunderDataUnavailable(
                f"Report data are incomplete for {slug}; regenerate the "
                "funder artifacts"
            ) from error
        return payload
