import collections
import warnings
from itertools import islice, chain
from elasticsearch.client import LicenseClient
import json
import faker
import hashlib
import getpass
import requests

try:
    # Import ABC from collections.abc for Python 3.4+
    from collections.abc import MutableMapping
except ImportError:
    # Fallback for Python 2
    from collections import MutableMapping
class ConfigParserError(Exception):
    pass
class CloudAPIError(Exception):
    pass

def flatten_nest(d, parent_key='', sep='.'):
    items = []
    for k, v in d.items():
        new_key = parent_key + sep + k if parent_key else k
        if isinstance(v, MutableMapping):
            items.extend(flatten_nest(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)

def parse_config(config):
    """first pass parsing of config file

    ensures that source and destination dicts exist, and sets types for reader and writer
    """
    source = config.get('source')
    dest = config.get('dest')
    anonymization_type = config.get('anonymization')
    masked_fields = config.get('include')
    suppressed_fields = config.get('exclude')
    include_rest = config.get('include_rest')
    sensitive = config.get('sensitive')

    if not source:
        raise ConfigParserError("source error: source not defined. Please check config.")
    if not dest:
        raise ConfigParserError("destination error: dest not defined. Please check config.")
    if not masked_fields:
        warnings.warn("no masked fields included in config. No data will be anonymized", Warning)

    reader_type = source.get('type')
    writer_type = dest.get('type')

    if not reader_type:
        raise ConfigParserError("source error: source type not defined. Please check config.")

    if not writer_type:
        raise ConfigParserError("destination error: dest type not defined. Please check config.")

    Config = collections.namedtuple('Config', 'source dest anonymization_type masked_fields suppressed_fields include_rest sensitive')
    config = Config(source, dest, anonymization_type, masked_fields, suppressed_fields, include_rest, sensitive)
    return config

def batch(iterable, size):
    sourceiter = iter(iterable)
    while True:
        batchiter = islice(sourceiter, size)
        try:
            yield chain([next(batchiter)], batchiter)
        except StopIteration:
            return

def faker_examples():
    providers = []
    examples = []
    f = faker.Faker()
    for provider in dir(faker.providers):
        if provider[0].islower():
            if provider == 'misc':
                continue
            try:
                for fake in dir(getattr(faker.providers, provider).Provider):
                    if fake[0].islower():
                        for i in range(5):
                            try:
                                examples.append(str(getattr(f, fake)()))
                                providers.append(fake)
                            except Exception as e:
                                print(e)
                                continue
            except:
                continue
    return providers, examples

def get_license_info(es):
    es_license = LicenseClient(es)
    license_info = es_license.get().get('license', {}).get('issued_to', None)
    if not license_info:
        return
    return license_info

def get_hashkey(es):
    license_info = get_license_info(es)
    if not license_info:
        return
    elif license_info == "Elastic Cloud":
        api_key = getpass.getpass('Elastic Cloud Console API Key: ')
        deployment_api_url = 'https://api.elastic-cloud.com/api/v1/deployments'
        request = requests.get(deployment_api_url, headers={'Authorization': f'ApiKey {api_key}'})
        if request.status_code != 200:
            raise CloudAPIError("Deployment API Authentication failed")
        deployments = json.loads(request.content)
        if len(deployments['deployments']) == 0:
            raise CloudAPIError("No deployments found")
        else:
            dep_id = deployments['deployments'][0]['id']
            request = requests.get(f'{deployment_api_url}/{dep_id}', headers={'Authorization': f'ApiKey {api_key}'})
            if request.status_code != 200:
                raise CloudAPIError("Deployment API Authentication failed")
            customer_id = json.loads(request.content).get('metadata', {}).get('owner_id', None)
            if not customer_id:
                raise CloudAPIError("Customer ID not found")
            return customer_id
    else:
        # For non-production clusters, the customer name is returned as "Company XYZ (non-production environments)"
        return license_info.split('(')[0].strip()

def contains_secret(regex, field_value):
    if type(field_value) == list:
        for f in field_value:
            if regex.search(f):
                return True
    elif regex.search(field_value):
        return True


def contains_keywords(field_value, keywords):
    if not keywords:
        return False
    if type(field_value) == list:
        for word in keywords:
            if any(word in f for f in field_value):
                return True
    elif any(word in field_value for word in keywords):
        return True

def hash_value(hashkey, field_value):
    return hashlib.sha256(f"{hashkey}:{field_value}".encode()).hexdigest()

def composite_query(field, size, query=None, term=""):
    body= {
            "size": 0,
            "aggs": {
                "my_buckets": {
                    "composite": {
                        "size": size,
                        "sources" : [
                            {field: {"terms": {"field": field}}}
                        ]
                    }
                }
            }
        }
    if term:
        body["aggs"]["my_buckets"]["composite"]["after"] = {field: term}
    if query:
        body['query'] = query
    return json.dumps(body)
