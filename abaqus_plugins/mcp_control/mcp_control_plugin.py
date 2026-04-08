# -*- coding: utf-8 -*-
"""
MCP Control Plugin - Adds GUI menu buttons to control MCP from kernel side.
"""

from abaqusGui import *
from abaqusConstants import ALL

# Get the plugin toolset
toolset = getAFXApp().getAFXMainWindow().getPluginToolset()

# Register Start MCP button - background thread mode
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
    description='Start MCP in blocking mode',
    helpUrl=''
)

# Register cooperative mode option
toolset.registerKernelMenuButton(
    buttonText='MCP|Start MCP (Cooperative)',
    moduleName='__main__',
    functionName='mcp_coop_loop()',
    icon=None,
    applicableModules=ALL,
    version='1.0',
    author='MCP Plugin',
    description='Start MCP in cooperative loop mode',
    helpUrl=''
)

# Register timer mode option (compat alias of background worker)
toolset.registerKernelMenuButton(
    buttonText='MCP|Start MCP (Timer)',
    moduleName='__main__',
    functionName='mcp_start_timer()',
    icon=None,
    applicableModules=ALL,
    version='1.0',
    author='MCP Plugin',
    description='Alias of background non-blocking mode',
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
    description='Stop MCP polling',
    helpUrl=''
)

# NOTE:
# Removed GUI-side automatic poll_once() timer thread to prevent GUI startup
# interruption when kernel-side MCP helpers are not loaded yet.
