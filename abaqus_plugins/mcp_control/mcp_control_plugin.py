# -*- coding: utf-8 -*-
"""
MCP Control Plugin - Adds GUI buttons to control MCP
"""

from abaqusGui import *
from abaqusConstants import ALL
import os

class MCPStopButton(AFXForm):
    def __init__(self, owner):
        AFXForm.__init__(self, owner)
        self.cmd = AFXGuiCommand(mode=self, method='mcp_stop', objectName='')

class MCPStartButton(AFXForm):
    def __init__(self, owner):
        AFXForm.__init__(self, owner)
        self.cmd = AFXGuiCommand(mode=self, method='mcp_start', objectName='')

# Get the plugin toolset
toolset = getAFXApp().getAFXMainWindow().getPluginToolset()

# Register Start MCP button
toolset.registerGuiMenuButton(
    buttonText='MCP|Start MCP',
    object=MCPStartButton(toolset),
    messageId=AFXMode.ID_ACTIVATE,
    icon=None,
    kernelInitString='',
    applicableModules=ALL,
    version='1.0',
    author='MCP Plugin',
    description='Start the MCP loop (non-blocking)',
    helpUrl=''
)

# Register Stop MCP button
toolset.registerGuiMenuButton(
    buttonText='MCP|Stop MCP',
    object=MCPStopButton(toolset),
    messageId=AFXMode.ID_ACTIVATE,
    icon=None,
    kernelInitString='',
    applicableModules=ALL,
    version='1.0',
    author='MCP Plugin',
    description='Stop the MCP loop',
    helpUrl=''
)
