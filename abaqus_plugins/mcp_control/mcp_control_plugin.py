# -*- coding: utf-8 -*-
"""
MCP Control Plugin - Adds GUI buttons to control MCP
"""

from abaqusGui import *
from abaqusConstants import ALL

# Get the plugin toolset
toolset = getAFXApp().getAFXMainWindow().getPluginToolset()

# Register Start MCP button - use mcp_start() for non-blocking
toolset.registerKernelMenuButton(
    buttonText='MCP|Start MCP (Background)',
    moduleName='__main__',
    functionName='mcp_start()',
    icon=None,
    applicableModules=ALL,
    version='1.0',
    author='MCP Plugin',
    description='Start MCP in background thread (non-blocking)',
    helpUrl=''
)

# Register blocking mode option
toolset.registerKernelMenuButton(
    buttonText='MCP|Start MCP (Blocking)',
    moduleName='__main__',
    functionName='mcp_loop()',
    icon=None,
    applicableModules=ALL,
    version='1.0',
    author='MCP Plugin',
    description='Start MCP in blocking mode (has Stop button)',
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
