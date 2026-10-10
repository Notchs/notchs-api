from app.core.config import settings
from app.db.session import build_connect_args, engine


def test_direct_connection_sets_search_path() -> None:
    server_settings = build_connect_args(use_pooler=False)["server_settings"]

    assert server_settings["search_path"] == settings.DB_SCHEMA
    assert server_settings["timezone"] == "UTC"


def test_pooler_connection_disables_statement_cache() -> None:
    args = build_connect_args(use_pooler=True)

    assert args["statement_cache_size"] == 0
    assert "server_settings" not in args
    assert args["prepared_statement_name_func"]() != args["prepared_statement_name_func"]()


def test_engine_maps_unqualified_tables_to_db_schema() -> None:
    assert engine.get_execution_options()["schema_translate_map"] == {None: settings.DB_SCHEMA}
