- POC project using LangGraph Deep Agents.
- A main agent will communicate with a weather agent that retrives weather data from https://openweathermap.org/api\
- You can get an API key from https://build.nvidia.com for a free llm model for the agents
- This is how the chatbot page looks like using a free model from build.nvidia.com # I ❤️ YOU NVIDIA!!!
- Note: In the .env, if you specify LLM_PROVIDER_SELECTOR=NVIDIA the code will not use Deep Agents (Anthropic-specific) but a plain LangGraph ReAct agent
<img width="1489" height="1073" alt="weather_chatbot" src="https://github.com/user-attachments/assets/3ccca3be-6353-4ebd-9f29-8e671b90aa01" />
Check out a live demo of the project at https://deep-agents-weather.onrender.com/
