from app import app
from app import DataLoader
from app.DashboardFilters import get_dashboard_filter_store
from gwasdiversitymonitor import _check_required_data


_check_required_data()
with DataLoader.published_data_lock() as published_path:
    get_dashboard_filter_store(published_path).warm()
