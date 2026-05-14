Create a Google Gemini multi-agent system that will plan a full itinerary from user input with an hour-by-hour breakdown and include travel time. 

Current simple iteration
- Takes in input in the form of a json string that has information about the country, city, activity. The input json might not always have the full location. An example might be "salt bread, seoul"
- Create Google Gemini agent to find information about that location. Have a research agent that will take some vague activity like "I want to go to a night market in Seoul" and find a few popular options for night markets within Seoul. The output from this agent should be put into a clean excel sheet with the possible addressses and locations for each activity. The research agent can find multiple entries for each activity for the user to look over and provide additional rating for the row. 
- Output that information in a way that would allow another application to put that into a spreadsheet for hour by hour planning. 


Tech stack
- python
- google's adk agent with gemini libraries


Special Requirements:
- Include unit tests of different quality inputs