Create a Google Gemini multi-agent system that will plan a full itinerary from user input with an hour-by-hour breakdown and include travel time. 


Core features
- take in a file with core information about the trip (country, city, flight information, days)
- Take in a csv file that will include information about what the user wants to do as well as how much the user wants to do that. It will include incomplete information, such as "korea, seoul, salt bread" or just "salt bread, seoul". 
- output an hour by hour breakdown with activity, recommended time to do activity, meals, travel buffers, rest times. There should be alternative plans proposed for taking it easy. 
- take in user input for which steps to do (start from beginning, or only itinerary step from file with some user feedback)
- This should be run locally in windows CLI from __main__ function. Provide examples on what the function call should look like and what the expected input and output are. 

Agents to include
- have a research agent that will take some vague activity like "I want to go to a night market in Seoul" and find a few popular options for night markets within Seoul. The output from this agent should be put into a clean excel sheet with the possible addressses and locations for each activity. The research agent can find multiple entries for each activity for the user to look over and provide additional rating for the row. 
- Itinerary agent that will create plan for each day. It will select the activities that the user wants to do most. It will look for activities that the user wants to do, take into account the travel time (ie bus, train, walking, driving) between each location. 



Tech stack
- python
- google's adk agent with gemini libraries


Special Requirements:

- Must include a detailed PRD (Product Requirement Document) covering user stories, UI layout, components, state management, API endpoints, error/loading states, accessibility considerations, folder structure, and a deployment plan for GitHub Pages
- Provide a numbered implementation checklist after the PRD
- DO NOT include actual code implementation — documentation and planning only