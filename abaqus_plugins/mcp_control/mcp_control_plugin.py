# -*- coding: utf-8 -*-
"""
MCP Control Plugin - Adds GUI buttons to control MCP

Uses GUI-side timer to implement non-blocking polling.
"""

from abaqusGui import *
from abaqusConstants import ALL
import os

# Global state
_mcp_timer_running = False


def _mcp_gui_tick(app):
    """GUI-side timer callback for non-blocking polling"""
    global _mcp_timer_running
    
    if not _mcp_timer_running:
        return
    
    # Check stop flag
    stop_file = os.path.expanduser('~/.abaqus-mcp/stop.flag')
    if os.path.exists(stop_file):
        try:
            os.remove(stop_file)
        except:
            pass
        _mcp_timer_running = False
        sendCommand('print("MCP: Stopped by stop.flag")')
        sendCommand('write_status("stopped", "Polling stopped")')
        return
    
    # Call kernel-side poll_once
    sendCommand('poll_once()')
    
    # Schedule next tick
    if _mcp_timer_running:
        app.afterTime(100, lambda: _mcp_gui_tick(app))


class MCPStartForm(AFXForm):
    """Form to start MCP loop (non-blocking)"""
    def __init__(self, owner):
        AFXForm.__init__(self, owner)
        
    def activate(self):
        global _mcp_timer_running
        
        if _mcp_timer_running:
            showAFXInfoDialog(getAFXApp().getAFXMainWindow(), 'MCP is already running.\n\nUse Plug-ins -> MCP -> Stop MCP to stop.')
            return True
        
        # Initialize on kernel side
        sendCommand('import __main__')
        sendCommand('if hasattr(__main__, "STOP_FILE") and os.path.exists(__main__.STOP_FILE): os.remove(__main__.STOP_FILE)')
        sendCommand('print("MCP: Listening for commands (non-blocking)...")')
        sendCommand('print("MCP: Use Plug-ins -> MCP -> Stop MCP to stop")')
        sendCommand('__main__.write_status("running", "Polling active")')
        
        # Start GUI-side timer
        _mcp_timer_running = True
        app = getAFXApp()
        app.afterTime(100, lambda: _mcp_gui_tick(app))
        
        return True


class MCPStopForm(AFXForm):
    """Form to stop MCP loop"""
    def __init__(self, owner):
        AFXForm.__init__(self, owner)
        
    def activate(self):
        global _mcp_timer_running
        _mcp_timer_running = False
        
        # Also set stop flag for kernel-side loop
        sendCommand('import __main__; __main__.mcp_stop()')
        return True


# Get the plugin toolset
toolset = getAFXApp().getAFXMainWindow().getPluginToolset()

# Register Start MCP button
toolset.registerGuiMenuButton(
    buttonText='MCP|Start MCP',
    object=MCPStartForm(toolset),
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
    object=MCPStopForm(toolset),
    messageId=AFXMode.ID_ACTIVATE,
    icon=None,
    kernelInitString='',
    applicableModules=ALL,
    version='1.0',
    author='MCP Plugin',
    description='Stop the MCP loop',
    helpUrl=''
)
