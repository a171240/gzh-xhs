# 对标文案提取

- 标题：\# OpenAI Python API library-2026-02-24
- 原文链接：https://raw.githubusercontent.com/openai/openai-python/main/README.md

## 摘要

原文链接**: https://raw.githubusercontent.com/openai/openai-python/main/README.md

## 正文

原文链接**: https://raw.githubusercontent.com/openai/openai-python/main/README.md
# OpenAI Python API library
application. The library includes type definitions for all request params and response fields,
and offers both synchronous and asynchronous clients powered by \httpx\.
It is generated from our \OpenAPI specification\ with \Stainless\.
The REST API documentation can be found on \platform.openai.com\. The full API of this library can be found in \api.md\.
\`\`\`sh
# install from PyPI
pip install openai
\`\`\
The full API of this library can be found in \api.md\.
The primary API for interacting with OpenAI models is the \Responses API\. You can generate text from the model with the code below.
\`\`\`python
import os
from openai import OpenAI
client = OpenAI(
This is the default and can be omitted
api\_key=os.environ.get("OPENAI\_API\_KEY"),
response = client.responses.create(
model="gpt-5.2",
instructions="You are a coding assistant that talks like a pirate.",
input="How do I check if a Python object is an instance of a class?",
print(response.output\_text)
The previous standard (supported indefinitely) for generating text is the \Chat Completions API\. You can use that API to generate text from the model with the code below.
client = OpenAI()
completion = client.chat.completions.create(
messages=\[\
{"role": "developer", "content": "Talk like a pirate."},\
{\
"role": "user",\
"content": "How do I check if a Python object is an instance of a class?",\
},\
\],
print(completion.choices\[0\].message.content)
we recommend using \python-dotenv\
to add \`OPENAI\_API\_KEY="My API Key"\` to your \`.env\` file
so that your API key is not stored in source control.
\Get an API key here\.
With an image URL:
img\_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d5/2023\_06\_08\_Raccoon1.jpg/1599px-2023\_06\_08\_Raccoon1.jpg"
input=\[\
"content": \[\
{"type": "input\_image", "image\_url": f"{img\_url}"},\
\],\
}\
With the image as a base64 encoded string:
import base64
with open("path/to/image.png", "rb") as image\_file:
b64\_image = base64.b64encode(image\_file.read()).decode("utf-8")
{"type": "input\_image", "image\_url": f"data:image/png;base64,{b64\_image}"},\
Simply import \`AsyncOpenAI\` instead of \`OpenAI\` and use \`await\` with each API call:
import asyncio
from openai import AsyncOpenAI
client = AsyncOpenAI(
async def main() -> None:
response = await client.responses.create(
model="gpt-5.2", input="Explain disestablishmentarianism to a smart five year old."
asyncio.run(main())
Functionality between the synchronous and asynchronous clients is otherwise identical.
You can enable this by installing \`aiohttp\`:
pip install openai\[aiohttp\]
Then you can enable it by instantiating the client with \`http\_client=DefaultAioHttpClient()\`:
from openai import DefaultAioHttpClient
async with AsyncOpenAI(
api\_key=os.environ.get("OPENAI\_API\_KEY"), # This is the default and can be omitted
) as client:
chat\_completion = await client.chat.completions.create(
"content": "Say this is a test",\
stream = client.responses.create(
input="Write a one-sentence bedtime story about a unicorn.",
stream=True,
for event in stream:
print(event)
The async client uses the exact same interface.
client = AsyncOpenAI()
async def main():
stream = await client.responses.create(
async for event in stream:
The Realtime API enables you to build low-latency, multi-modal conversational experiences. It currently supports text and audio as both input and output, as well as \function calling\ through a WebSocket connection.
Under the hood the SDK uses the \\`websockets\`\ library to manage connections.
The Realtime API works through a combination of client-sent events and server-sent events. Clients can send events to do things like update session configuration or send text and audio inputs. Server events confirm when audio responses have completed, or when a text response from the model has been received. A full event reference can be found \here\ and a guide can be found \here\.
Basic text based example:
\`\`\`py
async with client.realtime.connect(model="gpt-realtime") as connection:
await connection.session.update(
session={"type": "realtime", "output\_modalities": \["text"\]}
await connection.conversation.item.create(
item={
"type": "message",
"role": "user",
"content": \[{"type": "input\_text", "text": "Say hello!"}\],
await connection.response.create()
async for event in connection:
if event.type == "response.output\_text.delta":
print(event.delta, flush=True, end="")
elif event.type == "response.output\_text.done":
print()
elif event.type == "response.done":
break
However the real magic of the Realtime API is handling audio inputs / outputs, see this example \TUI script\ for a fully fledged example.
Whenever an error occurs, the Realtime API will send an \\`error\` event\ and the connection will stay open and remain usable. This means you need to handle it yourself, as \_no errors are raised directly\_ by the SDK when an \`error\` event comes in.
...
if event.type == 'error':
print(event.error.type)
print(event.error.code)
print(event.error.event\_id)
print(event.error.message)
\- Serializing back into JSON, \`model.to\_json()\
\- Converting to a dictionary, \`model.to\_dict()\
List methods in the OpenAI API are paginated.
all\_jobs = \[\]
# Automatically fetches more pages as needed.
for job in client.fine\_tuning.jobs.list(
limit=20,
):
Do something with job here
all\_jobs.append(job)
print(all\_jobs)
Or, asynchronously:
Iterate through items across all pages, issuing requests as needed.
async for job in client.fine\_tuning.jobs.list(
Alternatively, you can use the \`.has\_next\_page()\`, \`.next\_page\_info()\`, or \`.get\_next\_page()\` methods for more granular control working with pages:
first\_page = await client.fine\_tuning.jobs.list(
if first\_page.has\_next\_page():
print(f"will fetch next page using these details: {first\_page.next\_page\_info()}")
next\_page = await first\_page.get\_next\_page()
print(f"number of items we just fetched: {len(next\_page.data)}")
# Remove \`await\` for non-async usage.
Or just work directly with the returned data:
print(f"next page cursor: {first\_page.after}") # => "next page cursor: ..."
for job in first\_page.data:
print(job.id)
Nested parameters are dictionaries, typed using \`TypedDict\`, for example:
response = client.chat.responses.create(
"content": "How much ?",\
response\_format={"type": "json\_object"},
Request parameters that correspond to file uploads can be passed as \`bytes\`, or a \\`PathLike\`\ instance or a tuple of \`(filename, contents, media type)\`.
from pathlib import Path
client.files.create(
file=Path("input.jsonl"),
purpose="fine-tune",
The async client uses the exact same interface. If you pass a \\`PathLike\`\ instance, the file contents will be read asynchronously automatically.
Verifying webhook signatures is \_optional but encouraged\_.
For more information about webhooks, see \the API docs\.
Note that the \`body\` parameter must be the raw JSON string sent from the server (do not parse it first). The \`.unwrap()\` method will parse this JSON for you into an event object after verifying the webhook was sent from OpenAI.
from flask import Flask, request
app = Flask(\_\_name\_\_)
client = OpenAI() # OPENAI\_WEBHOOK\_SECRET environment variable is used by default
@app.route("/webhook", methods=\["POST"\])
def webhook():
request\_body = request.get\_data(as\_text=True)
try:
event = client.webhooks.unwrap(request\_body, request.headers)
if event.type == "response.completed":
print("Response completed:", event.data)
elif event.type == "response.failed":
print("Response failed:", event.data)
else:
print("Unhandled event type:", event.type)
return "ok"
except Exception as e:
print("Invalid signature:", e)
return "Invalid signature", 400
if \_\_name\_\_ == "\_\_main\_\_":
app.run(port=8000)
Note that the \`body\` parameter must be the raw JSON string sent from the server (do not parse it first). You will then need to parse the body after verifying the signature.
import json
client.webhooks.verify\_signature(request\_body, request.headers)
Parse the body after verification
event = json.loads(request\_body)
print("Verified event:", event)
When the API returns a non-success status code (that is, 4xx or 5xx
All errors inherit from \`openai.APIError\`.
import openai
client.fine\_tuning.jobs.create(
model="gpt-4o",
training\_file="file-abc123",
except openai.APIConnectionError as e:
print("The server could not be reached")
print(e.\_\_cause\_\_) # an underlying Exception, likely raised within httpx.
except openai.RateLimitError as e:
print("A 429 status code was received; we should back off a bit.")
except openai.APIStatusError as e:
print("Another non-200-range status code was received")
print(e.status\_code)
print(e.response)
Error codes are as follows:
\| Status Code \| Error Type \|
\| \-\-\-\-\-\-\-\-\-\-\- \| \-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\- \|
\| 400 \| \`BadRequestError\` \|
\| 401 \| \`AuthenticationError\` \|
\| 403 \| \`PermissionDeniedError\` \|
\| 404 \| \`NotFoundError\` \|
\| 429 \| \`RateLimitError\` \|
\| >=500 \| \`InternalServerError\` \|
\| N/A \| \`APIConnectionError\` \|
\> For more information on debugging requests, see \these docs\
input="Say 'this is a test'.",
print(response.\_request\_id) # req\_123
methods and modules are \_private\_.
\> \[!IMPORTANT\]
\> If you need to access request IDs for failed requests you must catch the \`APIStatusError\` exception
completion = await client.chat.completions.create(
messages=\[{"role": "user", "content": "Say this is a test"}\], model="gpt-5.2"
except openai.APIStatusError as exc:
print(exc.request\_id) # req\_123
raise exc
Certain errors are automatically retried 2 times by default, with a short exponential backoff.
429 Rate Limit, and >=500 Internal errors are all retried by default.
You can use the \`max\_retries\` option to configure or disable retry settings:
# Configure the default for all requests:
default is 2
max\_retries=0,
# Or, configure per-request:
client.with\_options(max\_retries=5).chat.completions.create(
"content": "How can I get the name of the current day in JavaScript?",\
By default requests time out after 10 minutes. You can configure this with a \`timeout\` option,
which accepts a float or an \\`httpx.Timeout\`\ object:
20 seconds (default is 10 minutes)
timeout=20.0,
# More granular control:
timeout=httpx.Timeout(60.0, read=5.0, write=10.0, connect=2.0),
# Override per-request:
client.with\_options(timeout=5.0).chat.completions.create(
"content": "How can I list all files in a directory using Python?",\
On timeout, an \`APITimeoutError\` is thrown.
Note that requests that time out are \retried twice by default\.
We use the standard library \\`logging\`\ module.
You can enable logging by setting the environment variable \`OPENAI\_LOG\` to \`info\`.
\`\`\`shell
$ export OPENAI\_LOG=info
Or to \`debug\` for more verbose logging.
In an API response, a field may be explicitly \`null\`, or missing entirely; in either case, its value is \`None\` in this library. You can differentiate the two cases with \`.model\_fields\_set\`:
if response.my\_field is None:
if 'my\_field' not in response.model\_fields\_set:
print('Got json like {}, without a "my\_field" key present at all.')
print('Got json like {"my\_field": null}.')
The "raw" Response object can be accessed by prefixing \`.with\_raw\_response.\` to any HTTP method call, e.g.,
response = client.chat.completions.with\_raw\_response.create(
messages=\[{\
}\],
print(response.headers.get('X-My-Header'))
completion = response.parse() # get the object that \`chat.completions.create()\` would have returned
print(completion)
These methods return a \\`LegacyAPIResponse\`\ object. This is a legacy class as we're changing it slightly in the next major version.
For the sync client this will mostly be the same with the exception
async client, all methods will be async.
be smooth.
The above interface eagerly reads the full response body when you make the request, which may not always be what you want.
To stream the response body, use \`.with\_streaming\_response\` instead, which requires a context manager and only reads the response body once you call \`.read()\`, \`.text()\`, \`.json()\`, \`.iter\_bytes()\`, \`.iter\_text()\`, \`.iter\_lines()\` or \`.parse()\`. In the async client, these are async methods.
As such, \`.with\_streaming\_response\` methods return a different \\`APIResponse\`\ object, and the async client returns an \\`AsyncAPIResponse\`\ object.
with client.chat.completions.with\_streaming\_response.create(
) as response:
print(response.headers.get("X-My-Header"))
for line in response.iter\_lines():
print(line)
The context manager is required so that the response will reliably be closed.
This library is typed for convenient access to the documented API.
To make requests to undocumented endpoints, you can make requests using \`client.get\`, \`client.post\`, and other
http verbs. Options on the client will be respected (such as retries) when making this request.
import httpx
response = client.post(
"/foo",
body={"my\_param": True},
print(response.headers.get("x-foo"))
If you want to explicitly send an extra param, you can do so with the \`extra\_query\`, \`extra\_body\`, and \`extra\_headers\` request
options.
can also get all the extra fields on the Pydantic model as a dict with
\\`response.model\_extra\`\.
You can directly override the \httpx client\ to customize it for your use case, including:
\- Custom \transports\
\- Additional \advanced\ functionality
from openai import OpenAI, DefaultHttpxClient
Or use the \`OPENAI\_BASE\_URL\` env var
),
You can also customize the client on a per-request basis by using \`with\_options()\`:
By default the library closes underlying HTTP connections whenever the client is \garbage collected\. You can manually close the client using the \`.close()\` method if desired, or with a context manager that closes when exiting.
with OpenAI() as client:
make requests here
# HTTP client is now closed
To use this library with \Azure OpenAI\, use the \`AzureOpenAI\
class instead of the \`OpenAI\` class.
\> The Azure API shape differs from the core API shape which means that the static types for responses / params
\> won't always be correct.
from openai import AzureOpenAI
# gets the API Key from environment variable AZURE\_OPENAI\_API\_KEY
client = AzureOpenAI(
api\_version="2023-07-01-preview",
model="deployment-name", # e.g. gpt-35-instant
"content": "How do I output all files in a directory using Python?",\
print(completion.to\_json())
\- \`azure\_endpoint\` (or the \`AZURE\_OPENAI\_ENDPOINT\` environment variable)
\- \`azure\_deployment\
\- \`api\_version\` (or the \`OPENAI\_API\_VERSION\` environment variable)
\- \`azure\_ad\_token\` (or the \`AZURE\_OPENAI\_AD\_TOKEN\` environment variable)
An example of using the client with Microsoft Entra ID (formerly known as Azure Active Directory) can be found \here\.
This package generally follows \SemVer\ conventions, though certain backwards-incompatible changes may be released as minor versions:
1\. Changes that only affect static types, without breaking runtime behavior.
2\. Changes to library internals which are technically public but not intended or documented for external use. \_(Please open a GitHub issue to let us know if you are relying on such internals.)\
3\. Changes that we do not expect to impact the vast majority of users in practice.
We take backwards-compatibility seriously and work hard to ensure you can rely on a smooth upgrade experience.
We are keen for your feedback; please open an \issue\ with questions, bugs, or suggestions.
If you've upgraded to the latest version but aren't seeing any new features you were expecting then your python environment is likely still using an older version.
You can determine the version that is being used at runtime with:
print(openai.\_\_version\_\_)
Python 3.9 or higher.
See \the contributing documentation\.
