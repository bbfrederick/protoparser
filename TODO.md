* Add debug mode that calculates and prints run timings for all major operations (reading and parsing an exar1 file, reading and parsing a pdf file, matching protocol or scan name, diffing two scans, writing a json file, writing an exar1 file).
* Add the ability to add or change Copy Parameters between scans in a protocol
* Implement an MCP server to let LLMs easily interact with Siemens protocol files
* Implement an Ollama front end to allow natural language manipulation of Siemens protocol files (requires the MCP server)
* Determine how add-ins are stored in protocol files and learn how to read and write them
