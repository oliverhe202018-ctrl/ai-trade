import re

# 1. Update brain_node.py to remove 'xtdata' from comment
content = open('brain_node.py', encoding='utf-8').read()
content = content.replace("xtdata doesn't always provide", "qmt provider doesn't always provide")
open('brain_node.py', 'w', encoding='utf-8').write(content)

# 2. Update test_market_provider_fault_injection.py
content = open('tests/test_market_provider_fault_injection.py', encoding='utf-8').read()
old_test = '''        # Mock tick missing volume and amount
        provider.xtdata.get_full_tick.return_value = {"sh600000": {"lastPrice": 10.0}}'''

new_test = '''        # Mock tick missing volume and amount
        provider.xtdata.get_full_tick.return_value = {"600000.SH": {"lastPrice": 10.0}}'''

content = content.replace(old_test, new_test)
open('tests/test_market_provider_fault_injection.py', 'w', encoding='utf-8').write(content)
