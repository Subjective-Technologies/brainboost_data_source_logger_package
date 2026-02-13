import requests

class Notifications:
    _cached_config = None  # Cache for configuration

    @classmethod
    def load_config_text(cls):
        """
        Fetches the raw configuration text from the global.config file in your GCS bucket.
        Uses the recommended public endpoint to avoid unnecessary redirects.
        """
        url = "https://storage.googleapis.com/brainboost_subjective_cloud_storage/global.config"
        response = requests.get(url, allow_redirects=True)
        response.raise_for_status()  # Raise an exception if the request failed
        return response.text

    @classmethod
    def parse_config(cls, config_text):
        """
        Parses the configuration text into a dictionary.
        """
        config = {}
        for line in config_text.splitlines():
            line = line.strip()
            # Skip blank lines or comments
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip()
        return config

    @classmethod
    def get_config(cls):
        """
        Returns the cached configuration if available; otherwise loads and caches it.
        """
        if cls._cached_config is None:
            config_text = cls.load_config_text()
            cls._cached_config = cls.parse_config(config_text)
        return cls._cached_config

    @classmethod
    def send_telegram_message(cls, message):
        """
        Sends a message via Telegram Bot API using the cached configuration.
        """
        config = cls.get_config()
        bot_token = config.get("brainboost_notifications_telegram_bot_token")
        chat_id = config.get("brainboost_notifications_telegram_bot_chat_id")

        if not bot_token or not chat_id:
            raise ValueError("Telegram bot token or chat id not found in configuration.")

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message
        }
        response = requests.post(url, data=payload)
        response.raise_for_status()
        return response.json()


# Example usage:
if __name__ == "__main__":
    try:
        result = Notifications.send_telegram_message("Hello from Terraform-configured app!")
        print("Message sent successfully:", result)
    except Exception as e:
        print("Error sending message:", e)
