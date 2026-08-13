import json
import os


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

            funders = payload.get("funders")
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
            return self._load_json(self.dashboard_path(slug))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise FunderDataUnavailable(
                f"Dashboard data are unavailable for {slug}"
            ) from error
