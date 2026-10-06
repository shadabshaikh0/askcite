from askcite.access import AccessRules
from askcite.brain import Brain
from askcite.config import AccessConfig
from askcite.slack_bot import build_app
from askcite.tools import Workspace


def test_app_registers_listeners_without_network(settings, catalog):
    brain = Brain(Workspace(settings, store=None, catalog=catalog, runner=None), cloud=None)
    app = build_app(brain, AccessRules(AccessConfig()), token="xoxb-test", token_verification_enabled=False)
    assert app._listeners and len(app._listeners) == 5  # mention, DM, show query, approve, reject
