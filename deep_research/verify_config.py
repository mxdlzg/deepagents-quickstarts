#!/usr/bin/env python3
"""Verify OpenAI model configuration.

This script checks that all required environment variables are set
and the OpenAI model can be initialized successfully.
"""

import os
import sys

from dotenv import load_dotenv

# Load environment variables
load_dotenv()


def check_config():
    """Check OpenAI configuration and print results."""
    print("=" * 60)
    print("OpenAI Model Configuration Verification")
    print("=" * 60)

    errors = []
    warnings = []

    # Check required OPENAI_API_KEY
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        errors.append("❌ OPENAI_API_KEY is not set")
    else:
        # Mask the key for display
        masked_key = api_key[:10] + "..." + api_key[-5:] if len(api_key) > 15 else "***"
        print(f"✓ OPENAI_API_KEY: {masked_key}")

    # Check optional parameters
    model = os.getenv("OPENAI_MODEL", "gpt-4o")
    print(f"✓ OPENAI_MODEL: {model}")

    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    print(f"✓ OPENAI_BASE_URL: {base_url}")

    temperature = os.getenv("OPENAI_TEMPERATURE", "0.0")
    print(f"✓ OPENAI_TEMPERATURE: {temperature}")

    top_p = os.getenv("OPENAI_TOP_P", "1.0")
    print(f"✓ OPENAI_TOP_P: {top_p}")

    max_tokens = os.getenv("OPENAI_MAX_TOKENS")
    if max_tokens:
        print(f"✓ OPENAI_MAX_TOKENS: {max_tokens}")
    else:
        print("ℹ OPENAI_MAX_TOKENS: not set (using model default)")

    # Check Tavily API key
    tavily_key = os.getenv("TAVILY_API_KEY")
    if not tavily_key:
        errors.append("❌ TAVILY_API_KEY is not set")
    else:
        masked_tavily = tavily_key[:5] + "..." + tavily_key[-5:] if len(tavily_key) > 10 else "***"
        print(f"✓ TAVILY_API_KEY: {masked_tavily}")

    print("\n" + "=" * 60)

    # Try to initialize the model
    if not errors:
        print("Attempting to initialize ChatOpenAI model...")
        try:
            from langchain_openai import ChatOpenAI

            model_kwargs = {
                "api_key": api_key,
                "model": model,
                "temperature": float(temperature),
                "top_p": float(top_p),
            }

            if base_url and base_url != "https://api.openai.com/v1":
                model_kwargs["base_url"] = base_url

            if max_tokens:
                model_kwargs["max_tokens"] = int(max_tokens)

            chat_model = ChatOpenAI(**model_kwargs)
            print(f"✓ ChatOpenAI model initialized successfully!")
            print(f"  Model: {chat_model.model_name}")
            print(f"  Temperature: {chat_model.temperature}")
            print(f"  Top P: {chat_model.top_p}")

        except Exception as e:
            errors.append(f"❌ Failed to initialize ChatOpenAI: {str(e)}")

    print("\n" + "=" * 60)

    if errors:
        print("ERRORS:")
        for error in errors:
            print(f"  {error}")
        print("\nPlease fix the errors above and try again.")
        return False

    if warnings:
        print("WARNINGS:")
        for warning in warnings:
            print(f"  {warning}")

    print("✓ Configuration is valid!")
    return True


if __name__ == "__main__":
    success = check_config()
    sys.exit(0 if success else 1)
