"""
Exceptions métier du service d'optimisation.
"""


class SiteNotFoundError(Exception):
    """Levée quand le site_id demandé n'existe pas en base."""


class ForecastsMissingError(Exception):
    """Levée quand plus de 50 % des forecasts (conso ou PV) sont absents sur l'horizon."""


class InfeasibleProblemError(Exception):
    """Levée quand le solveur LP échoue (infaisable même avec slack actif)."""


class UnauthorizedError(Exception):
    """Levée quand la clé API ne correspond pas au site_id de la requête."""
