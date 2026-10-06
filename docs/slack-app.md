# Creating the Slack app

1. Go to **https://api.slack.com/apps**, then **Create New App → From a manifest**, and pick your workspace.
2. Paste this manifest (YAML tab), then **Next → Create**:

```yaml
display_information:
  name: Askcite
  description: Answers questions from our code, docs and data
features:
  app_home:
    messages_tab_enabled: true
    messages_tab_read_only_enabled: false
  bot_user:
    display_name: askcite
    always_online: true
oauth_config:
  scopes:
    bot: [app_mentions:read, chat:write, im:history, im:read, im:write, usergroups:read]
settings:
  event_subscriptions:
    bot_events: [app_mention, message.im]
  interactivity:
    is_enabled: true
  socket_mode_enabled: true
  org_deploy_enabled: false
  token_rotation_enabled: false
```

3. **Basic Information → App-Level Tokens → Generate Token and Scopes**: add the scope `connections:write`
   and copy the `xapp-…` token.
4. **OAuth & Permissions → Install to Workspace**: copy the `xoxb-…` bot token.
5. On Askcite's **Connectors** page, choose Slack → Connect, paste both tokens, then **Test** and **Save**.
   Or run `askcite connect slack`.

The bot connects within a few seconds; no restart is needed. DM it, or invite it to a channel with
`/invite @askcite` and mention it.
