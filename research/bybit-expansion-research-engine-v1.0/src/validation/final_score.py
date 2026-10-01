class ValidationScore:
    """Compatibility facade for callers of the release-candidate API."""
    def calculate(self, results):
        from src.validation.walk_forward import walk_forward
        return walk_forward(results)
