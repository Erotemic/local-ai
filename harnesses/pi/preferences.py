"""Shared settings policy used by the host doctor and guest setup."""
import copy

PACKAGE_VERSIONS = {'pi-provider-litellm': '4.4.2', 'pi-effort': '0.1.1', 'pi-subagents': '0.89.0'}
MIN_PI_VERSION = (1, 4, 0)  # Recipe-tested baseline, not a claim about upstream minimums.


def desired_preferences(baseline, provider, model, effort=None):
    result = copy.deepcopy(baseline)
    if provider:
        result['defaultProvider'] = provider
    if model:
        result['defaultModel'] = model
        if provider:
            level = effort or result['defaultThinkingLevel']
            key = provider + '/' + model
            result['modelThinkingLevels'] = {key: level}
            result['pi-effort'] = {'modelEffort': {key: level}}
    if effort:
        result['defaultThinkingLevel'] = effort
    result.setdefault('subagents', {})['defaultThinking'] = result['defaultThinkingLevel']
    return result


def differences(current, desired, prefix=''):
    result = []
    for key, value in desired.items():
        path = prefix + key
        existing = current.get(key) if isinstance(current, dict) else None
        if isinstance(value, dict):
            result.extend(differences(existing, value, path + '.'))
        elif existing != value:
            result.append((path, value))
    return result


def merge(current, desired):
    result = copy.deepcopy(current)
    for key, value in desired.items():
        if isinstance(value, dict):
            existing = result.get(key)
            result[key] = merge(existing if isinstance(existing, dict) else {}, value)
        else:
            result[key] = copy.deepcopy(value)
    return result
