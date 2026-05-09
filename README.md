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
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

   __            __    _      __            __  
  / /  ___ ____ / /_  | | /| / /__  _______/ /__
 / /__/ _ `(_-</ __/  | |/ |/ / _ \/ __/ _  (_-<
/____/\_,_/___/\__/   |__/|__/\___/_/  \_,_/___/
                                                
- This is how the chatbot page looks like using a free model from build.nvidia.com # I ❤️ YOU NVIDIA!!!
<img width="1489" height="1073" alt="weather_chatbot" src="https://github.com/user-attachments/assets/3ccca3be-6353-4ebd-9f29-8e671b90aa01" />
Check out a live demo of the project at https://deep-agents-weather.onrender.com/