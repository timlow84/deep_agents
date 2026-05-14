   ____                              
  / __/_ ____ _  __ _  ___ _______ __
 _\ \/ // /  ' \/  ' \/ _ `/ __/ // /
/___/\_,_/_/_/_/_/_/_/\_,_/_/  \_, / 
                              /___/  

- POC project using LangGraph Deep Agents.
- A main agent will communicate with a weather agent that retrives weather data from https://openweathermap.org/api\
- You can get an API key from https://build.nvidia.com for a free llm model for the agents
- 
- Note: In the .env, if you specify LLM_PROVIDER_SELECTOR=NVIDIA the code will not use Deep Agents (Anthropic-specific) but a plain LangGraph ReAct agent


   ____    __              _____                              __  
  / __/__ / /___ _____    / ___/__  __ _  __ _  ___ ____  ___/ /__
 _\ \/ -_) __/ // / _ \  / /__/ _ \/  ' \/  ' \/ _ `/ _ \/ _  (_-<
/___/\__/\__/\_,_/ .__/  \___/\___/_/_/_/_/_/_/\_,_/_//_/\_,_/___/
                /_/                                               
uv pip install -r requirements.txt



    __  __                 ______         ____            
   / / / /___ _      __   /_  __/___     / __ \__  ______ 
  / /_/ / __ \ | /| / /    / / / __ \   / /_/ / / / / __ \
 / __  / /_/ / |/ |/ /    / / / /_/ /  / _, _/ /_/ / / / /
/_/ /_/\____/|__/|__/    /_/  \____/  /_/ |_|\__,_/_/ /_/ 
                                                                                                               
(1) To run the server hosting the chat page, use the command below:
uv run python -m uvicorn app.main:app --host 0.0.0.0 --reload

###############################################################
# A2A Agents
###############################################################

(2) A2A Weather Agent
- Port is read from A2A_WEATHER_AGENT_PORT in .env (default: 9001)

Start the A2A Weather Agent server:
uv run python agents/a2a/weather_agent/a2a_weather_agent.py

Discover the agent card:
curl http://localhost:9001/.well-known/agent-card.json

Send a message (current weather):
curl -X POST http://localhost:9001/ \
  -H "Content-Type: application/json" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":\"1\",\"method\":\"message/send\",\"params\":{\"message\":{\"role\":\"user\",\"parts\":[{\"kind\":\"text\",\"text\":\"What is the weather in Singapore?\"}]}}}"

Call from another A2A agent (Python):
  from a2a.client import A2AClient
  client = await A2AClient.get_client_from_agent_card_url(
      httpx.AsyncClient(), "http://localhost:9001/.well-known/agent-card.json"
  )
  response = await client.send_message(
      SendMessageRequest(message=Message(role="user", parts=[Part(text=TextPart(text="Weather in Tokyo?"))]))
  )

(3.1) A2A Orchestrator Agent Server
- Port is read from A2A_ORCHESTRATOR_AGENT_PORT in .env (default: 9000)
- Requires the A2A Weather Agent to be running first
- Listens to incoming messages from a Kafka Topic

Start the A2A Orchestrator Agent server:
uv run python agents/a2a/orchestrator_agent/a2a_orchestrator_agent.py

Discover the agent card:
curl http://localhost:9000/.well-known/agent-card.json

Send a message (delegates to weather agent):
curl -X POST http://localhost:9000/ \
  -H "Content-Type: application/json" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":\"1\",\"method\":\"message/send\",\"params\":{\"message\":{\"role\":\"user\",\"parts\":[{\"kind\":\"text\",\"text\":\"What is the weather in Singapore?\"}]}}}"

(3.2) Orchestrator Agent Client
- A LangGraph ReAct agent that delegates weather queries to the A2A Weather Agent
- Requires the A2A Weather Agent to be running first

Run the orchestrator client (interactive agent):
uv run python agents/a2a/orchestrator_agent/a2a_orchestrator_client.py


   __            __    _      __            __  
  / /  ___ ____ / /_  | | /| / /__  _______/ /__
 / /__/ _ `(_-</ __/  | |/ |/ / _ \/ __/ _  (_-<
/____/\_,_/___/\__/   |__/|__/\___/_/  \_,_/___/
                                                
- This is how the chatbot page looks like using a free model from build.nvidia.com # I ❤️ YOU NVIDIA!!!
<img width="1489" height="1073" alt="weather_chatbot" src="https://github.com/user-attachments/assets/3ccca3be-6353-4ebd-9f29-8e671b90aa01" />
Check out a live demo of the project at https://deep-agents-weather.onrender.com/

- The ASCII Art is generated from the link below using the font "Slant"...
- https://patorjk.com/software/taag/#p=display&f=Slant&t=How+To+Run&x=none&v=4&h=4&w=80&we=false