# Mid-Columbia Fisheries Data Analysis Project - AI Agent Instructions

## Project Overview
A data ingestion and analysis tool for Project Managers and Biologists at the Mid-Columbia Fisheries. The tool is meant to help organize small data files that are downloaded from field devices, collate and analyze the data, and provide easy to use data visualization tools.

## User Interaction
Users will be fisheries biologists and project managers who are performaing active restoration on a variety of streams and rivers. Before actually starting on the restoration, they need to establish of baseline of stream temperatures and stream flows for one or more specifi reaches of a stream. They do this buy establishing multiple monitoring stations that are placed along the reach. The stations have wells and data loggers in the wells that collect the following information (not every station collects all the data, some may only collect one or two parameters): air temperature, air pressure, water temperature and water pressure. The water pressure measurements are taken both in-stream by data loggers dropped into wells driven into the stream bed, and out of stream by data loggers dropped into wells driven near the streams. Each station will have one or more in-stream wells and one or more out of stream wells. Each reach will have at least one data logger that is designated to capture atmospheric pressure. The atmospheric pressure readings will be combined with the in-stream and out of stream pressure readings to calculate water depth.

One ofthe problems the users have with a project like this is managing the data. The loggers generally collect data once per hour, 24 hours per day, 365 days per year. A project may collect data for multiple years. The data is retrieved by pulling the device out of the well and using the device makers app to wireless download the data into a .csv file, one file for each logger, and then that .csv data needs to be added to all the existing data for that site. My idea is that we will create a directory structure that looks like:

data/
    Project 1/
        Project Settings JSON file
        Reach 1/
            ATM/
                Atmospheric Logger .csv files
            Site 1/
                IS 1/
                    In-stream 1 logger .csv files
                OS 1/
                    Out of stream logger .csv files
            Site 2/
                IS 2a/
                    Instream 2a logger files
                IS 2b/
                    Instream 2b logger files
                OS 1/
                    Out of stream 1 logger files
                OS 2/
                    Out of stream 2 logger files
            Site 3/
            ...
            Site n/
                    ...
                IS 1/
                    ...
                OS 1/
                    ...
        Reach 2/
            ATM/
                Atmospheric Logger .csv files
            Site 1/
                ...
            ...
        ...
    ...

A few notes:
1. Each Reach must have a ATM
2. Reaches will have at least one site but most will have at least 5 and no more than 100
3. A site will have at least on instream well or one out of stream well but can have any number of either
4. We need to be able to give Projects, Reaches, and Sites unique alpha-number names. Names can contains spaces and punctuation.

This structure may live on a local drive or it may live on a Google Drive or OneDrive. 

I want the user to be able to drop new data that is downloaded into the appropriate directory and then next time the app is run new data is automatically picked up and added to existign data for each site.

We need to make the logger handlers modular so that if new logger types are used we can quickly add a new handler for that logger. This means we need to abstract the logger data to hide the details as we get into the processing and UI display parts of the project.

The User will primarily interact with the data through a Browser. We can initially serve the browser data locally although later we may move the app to AWS or other service (that is out of scope for now). I want the primary display to show a list of defined projects and the reaches and sites belonging to that project (in heirarchical format) on the left and a map view on the right. When the user selects a Reach inside of a project on the left, the map view changes to the location of the project showing all the sites with some sort of iconography that we will define later. We can start with dots for now.

As the user hovers over the individual sites, they see a popup that shows the:

1. Reach name
2. Site name
3. Individual well name
4. Numbr of data points
5. Last data point.

When the user clicks on an individual site, we will open up a detailed data view that we will define later.

The user also needs to be able to add, manage and delete sites. We need to create a UI for that. All setting should be stored inside the site in JSON format (JSON5, it's OK to add comments to document settings).

### Technology
I expect to create this app in Python. Use uv to manage the python virtual environment. I am fine with Javascript or any appropriate browser framework to make the UI modern and professional looking.

### Error Handling
1. Errors must be handled, not just ignored
2. If None is returned, make sure it is handled by the calling function

## Testing
- **Create unit tests for key functions**: All key functions shoudl have unit tests. Let's use pytest.
- **Run all tests**: Run all tests and verify all pass. Run tests with: uv run pytest
- **Test issues**: fix any test issues and re-run the tests until they pass

## Behavior
1. Don’t assume. Admit when you are unsure. Ask for help when needed. Surface tradeoffs.
2. Minimum code that solves the problem. Nothing speculative.
3. Touch only what you must. Simple is better. Reuse when possible. Remove unused code and variables. Clean up after yourself.
4. Define success criteria. Loop until verified.
