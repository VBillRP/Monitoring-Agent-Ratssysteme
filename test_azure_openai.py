import asyncio
import os
from openai import AsyncAzureOpenAI


async def main():
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT")
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")

    print("Checking Azure OpenAI configuration...")

    if not api_key:
        raise Exception("Missing AZURE_OPENAI_API_KEY")

    if not endpoint:
        raise Exception("Missing AZURE_OPENAI_ENDPOINT")

    if not deployment:
        raise Exception("Missing AZURE_OPENAI_DEPLOYMENT")

    if not api_version:
        raise Exception("Missing AZURE_OPENAI_API_VERSION")

    print("All required settings are present.")
    print("Sending test request to Azure OpenAI...")

    client = AsyncAzureOpenAI(
        api_key=api_key,
        azure_endpoint=endpoint,
        api_version=api_version,
    )

    response = await client.chat.completions.create(
        model=deployment,
        messages=[
            {
                "role": "system",
                "content": "You are a helpful assistant."
            },
            {
                "role": "user",
                "content": "Reply with only this exact text: Azure OpenAI works"
            },
        ],
        temperature=0,
        max_tokens=20,
    )

    answer = response.choices[0].message.content

    print("Azure OpenAI response:")
    print(answer)


if __name__ == "__main__":
    asyncio.run(main())
