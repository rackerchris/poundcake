"""Dummy service plugin for exercising the service-plugin contract."""

from api.plugins.dummy.templates import DUMMY_INGREDIENT_TEMPLATES, DUMMY_RECIPE_TEMPLATES

__all__ = ["DUMMY_INGREDIENT_TEMPLATES", "DUMMY_RECIPE_TEMPLATES"]
