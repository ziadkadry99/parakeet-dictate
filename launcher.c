// Native entry point for Parakeet Dictate.app.
// Embeds Python in-process (no exec), so macOS attributes Microphone, Input Monitoring
// and Accessibility permissions to this app, not to the shared python interpreter.
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdlib.h>

int main(int argc, char **argv) {
    setenv("HF_HUB_OFFLINE", "1", 1);  // model is cached; don't need network at login

    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    // Pretend to be the venv's python so its pyvenv.cfg / site-packages are picked up.
    PyStatus st = PyConfig_SetBytesString(&config, &config.program_name, VENV_PYTHON);
    if (!PyStatus_Exception(st))
        st = PyConfig_SetBytesString(&config, &config.run_filename, argc > 1 ? argv[1] : SCRIPT);
    if (!PyStatus_Exception(st))
        st = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(st))
        Py_ExitStatusException(st);
    return Py_RunMain();
}
