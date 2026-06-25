import yaml

with open('config/config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

config['news_data'] = {
  'enabled': True,
  'readonly': True,
  'providers': {
    'cninfo': {
      'enabled': True,
      'poll_interval_seconds': 300,
      'timeout_seconds': 8
    },
    'cls': {
      'enabled': True,
      'poll_interval_seconds': 60,
      'timeout_seconds': 5
    }
  },
  'store': {
    'type': 'sqlite',
    'path': 'data_cache/news_events.db'
  },
  'health_file': 'data_cache/news_health.json',
  'allow_trade_trigger': False,
  'allow_state_mutation': False
}

with open('config/config.yaml', 'w', encoding='utf-8') as f:
    yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
