#!/usr/bin/env python3
import json
import re
import sys

with open(sys.argv[1], encoding='utf-8') as stream:
    features = json.load(stream)
for key, value in sorted(features.items()):
    if not re.fullmatch(r'with_[a-z0-9_]+', key) or type(value) is not bool:
        raise ValueError('feature 이름 또는 boolean 값이 잘못되었습니다.')
    print(f'{key.upper()}={str(value).lower()}')
