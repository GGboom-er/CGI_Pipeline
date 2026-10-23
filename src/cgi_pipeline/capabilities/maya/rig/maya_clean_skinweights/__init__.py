"""Host-safe namespace; the external numerical worker never imports Maya."""


def execute(payload):
    from .maya_clean_skinweights import execute as implementation
    return implementation(payload)
