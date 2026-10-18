import requests

def publish_or_update_standings(stage_id, data):
    webhook_url = data['webhook_url']  # salvato nel DB
    message_id = data.get('discord_message_id')

    embed = build_embed(stage_id, data)

    if message_id:
        # PATCH per modificare il messaggio esistente
        parts = webhook_url.rstrip('/').split('/')
        edit_url = f"https://discord.com/api/v10/webhooks/{parts[-2]}/{parts[-1]}/messages/{message_id}"
        requests.patch(edit_url, json={"embeds": [embed]})
    else:
        # POST per creare il messaggio e salvare l'ID
        resp = requests.post(webhook_url + "?wait=true", json={"embeds": [embed]})
        if resp.status_code == 200:
            new_id = resp.json()['id']
            # Salva new_id nel DB per gli aggiornamenti futuri