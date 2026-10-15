"""Validate the deployment dependency graph, not just Python code."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    # Check explicit keys before resolving YAML merge keys.
    names = [key.value for key, _ in node.value if key.value != '<<']
    assert len(names) == len(set(names)), f'Duplicate configuration keys: {names}'
    loader.flatten_mapping(node)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def test_production_services_have_valid_startup_dependencies():
    config = yaml.load((ROOT / 'docker-compose.yml').read_text(), Loader=UniqueKeyLoader)
    services = config['services']
    assert {'app', 'worker', 'dispatcher', 'migrate', 'postgres', 'redis', 'caddy'} == set(services)
    assert services['migrate']['depends_on'] == {'postgres': {'condition': 'service_healthy'}}
    for name in ('app', 'worker', 'dispatcher'):
        assert services[name]['depends_on']['migrate']['condition'] == 'service_completed_successfully'
        assert services[name]['depends_on']['redis']['condition'] == 'service_healthy'
    assert services['worker']['stop_grace_period'] == '660s'
    assert 'worker_health.py' in str(services['worker']['healthcheck'])
    for service in services.values():
        assert set(service.get('depends_on', {})) <= set(services)
