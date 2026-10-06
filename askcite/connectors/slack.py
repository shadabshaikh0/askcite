from __future__ import annotations

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from askcite.config import Settings, SlackSource, SourcesConfig
from askcite.connectors.base import Connector, Field, TestResult


class SlackConnector(Connector):
    kind = "slack"
    title = "Slack"
    description = "Where people ask questions. Uses Socket Mode, so no public URL is needed."
    fields = [
        Field("app_token", "App-level token", "password", required=True, secret=True, placeholder="xapp-…",
              help="Basic Information → App-Level Tokens (scope connections:write)."),
        Field("bot_token", "Bot token", "password", required=True, secret=True, placeholder="xoxb-…",
              help="OAuth & Permissions → Bot User OAuth Token."),
        Field("allowed_channels", "Allowed channel IDs", "list", placeholder="C0123ABC, C0456DEF",
              help="Leave empty to answer in any channel the bot is invited to. DMs are always allowed."),
        Field("data_users", "People allowed to ask database questions", "list", placeholder="U0123ABC",
              help="Slack member IDs (Profile → ⋮ → Copy member ID)."),
        Field("approvers", "Approvers", "list", placeholder="U0123ABC",
              help="Members who can approve queries on sensitive tables."),
    ]

    def test(self, config: dict, secrets: dict, settings: Settings) -> TestResult:
        try:
            auth = WebClient(token=secrets.get("bot_token")).auth_test()
        except SlackApiError as error:
            return TestResult(False, f"Bot token rejected by Slack: {error.response.get('error')}")
        try:
            WebClient(token=secrets.get("app_token")).apps_connections_open()
        except SlackApiError as error:
            return TestResult(False, f"App-level token rejected: {error.response.get('error')} "
                                     "(it needs the connections:write scope and Socket Mode enabled)")
        return TestResult(True, f"Connected to workspace “{auth.get('team')}” as @{auth.get('user')}",
                          details=["Socket Mode connection works"])

    def apply(self, sources: SourcesConfig, name: str, config: dict, secrets: dict) -> None:
        sources.slack = SlackSource(app_token=secrets.get("app_token"), bot_token=secrets.get("bot_token"),
                                    allowed_channels=config.get("allowed_channels") or [],
                                    data_users=config.get("data_users") or [],
                                    approvers=config.get("approvers") or [], origin="connector")
