const vscode = require("vscode");

function jiraTools() {
  return vscode.lm.tools.filter((tool) =>
    /jira|atlassian|jql/i.test(
      `${tool.name} ${tool.description || ""}`
    )
  );
}

function activate(context) {
  context.subscriptions.push(
    vscode.commands.registerCommand(
      "jiraMcpRunner.listTools",
      async () => {
        const output = vscode.window.createOutputChannel("Jira MCP Runner");
        const tools = jiraTools();

        if (tools.length === 0) {
          output.appendLine(
            "No matching tools found. Check that the Jira MCP server is connected in this VS Code window."
          );
        }

        for (const tool of tools) {
          output.appendLine(`
NAME: ${tool.name}`);
          output.appendLine(`DESCRIPTION: ${tool.description || ""}`);
          output.appendLine(
            `INPUT SCHEMA: ${JSON.stringify(tool.inputSchema, null, 2)}`
          );
        }

        output.show();
      }
    ),

    vscode.commands.registerCommand(
      "jiraMcpRunner.callTool",
      async () => {
        const tools = jiraTools();

        if (tools.length === 0) {
          vscode.window.showErrorMessage(
            "No Jira MCP tools found. Connect the server and run List Available Tools first."
          );
          return;
        }

        const selected = await vscode.window.showQuickPick(
          tools.map((tool) => ({
            label: tool.name,
            description: tool.description,
            tool
          })),
          { placeHolder: "Choose the Jira MCP tool to call" }
        );

        if (!selected) return;

        const rawInput = await vscode.window.showInputBox({
          prompt: `Enter JSON input for ${selected.tool.name}`,
          placeHolder: '{"jql":"project = ABC ORDER BY updated DESC"}',
          value: "{}",
          ignoreFocusOut: true
        });

        if (rawInput === undefined) return;

        let input;
        try {
          input = JSON.parse(rawInput);
          if (input === null || Array.isArray(input) || typeof input !== "object") {
            throw new Error("Input must be a JSON object");
          }
        } catch (error) {
          vscode.window.showErrorMessage(`Invalid JSON: ${error.message}`);
          return;
        }

        const saveUri = await vscode.window.showSaveDialog({
          defaultUri: vscode.Uri.joinPath(
            context.globalStorageUri,
            "jira-mcp-result.json"
          ),
          filters: { JSON: ["json"] }
        });

        if (!saveUri) return;

        try {
          const result = await vscode.lm.invokeTool(selected.tool.name, {
            input
          });

          const saved = {
            tool: selected.tool.name,
            input,
            result
          };

          await vscode.workspace.fs.writeFile(
            saveUri,
            Buffer.from(JSON.stringify(saved, null, 2), "utf8")
          );

          vscode.window.showInformationMessage(
            "Jira MCP result saved as JSON."
          );
        } catch (error) {
          vscode.window.showErrorMessage(
            `Jira MCP call failed: ${error.message}`
          );
        }
      }
    )
  );
}

function deactivate() {}

module.exports = { activate, deactivate };