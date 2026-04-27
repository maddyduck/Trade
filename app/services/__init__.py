"""Service layer — all business logic that's not tied to a single model.

Views stay thin: they parse input, call a service, render a response.
Services return domain objects or raise typed exceptions.
"""
