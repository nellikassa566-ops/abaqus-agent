# -*- coding: utf-8 -*-
"""
MCP Control Plugin - Adds GUI buttons to control MCP
"""

from abaqusGui import *
from abaqusConstants import ALL

# Get the plugin toolset
toolset = getAFXApp().getAFXMainWindow().getPluginToolset()

# Register Start MCP button - directly call mcp_loop()
toolset.registerKernelMenuButton(
    buttonText='MCP|Start MCP',
    moduleName='__main__',
    functionName='mcp_loop()',
    icon=None,
    applicableModules=ALL,
    version='1.0',
    author='MCP Plugin',
    description='Start the MCP loop (use Stop button at bottom-left to stop)',
    helpUrl=''
)

# Register Stop MCP button
toolset.registerKernelMenuButton(
    buttonText='MCP|Stop MCP',
    moduleName='__main__',
    functionName='mcp_stop()',
    icon=None,
    applicableModules=ALL,
    version='1.0',
    author='MCP Plugin',
    description='Stop the MCP loop',
    helpUrl=''
)
