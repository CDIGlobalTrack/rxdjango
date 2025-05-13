#include <Python.h>
#include <string.h>

static PyObject* generate_delta(PyObject* self, PyObject* args) {
    PyObject *original, *instance;
    PyObject *key, *old_value, *new_value;
    Py_ssize_t pos = 0;
    int empty = 1;

    if (!PyArg_ParseTuple(args, "OO", &original, &instance)) {
        return NULL;
    }

    if (!PyDict_Check(original) || !PyDict_Check(instance)) {
        PyErr_SetString(PyExc_TypeError, "Both arguments must be dictionaries");
        return NULL;
    }

    // Iterate through original dictionary
    while (PyDict_Next(original, &pos, &key, &old_value)) {
        // Skip if key is 'id' or starts with '_'
        if (PyUnicode_Check(key)) {
            const char *key_str = PyUnicode_AsUTF8(key);
            if (strcmp(key_str, "id") == 0 || key_str[0] == '_') {
                continue;
            }
        }

        // Get the new value from instance
        new_value = PyDict_GetItem(instance, key);
        if (!new_value) {
            continue;  // Key not found, skip as in original Python code
        }

        // Compare values
        int cmp = PyObject_RichCompareBool(old_value, new_value, Py_EQ);
        if (cmp == -1) {
            return NULL;  // Error in comparison
        }

        if (cmp == 1) {
            // Values are equal, remove from instance copy
            if (PyDict_DelItem(instance, key) == -1) {
                return NULL;
            }
        } else {
            // Values are different
            empty = 0;
        }
    }

    // Create a new list for deltas
    PyObject *deltas = PyList_New(0);
    if (!deltas) {
        return NULL;
    }

    // If not empty, append to deltas
    if (!empty) {
        if (PyList_Append(deltas, instance) == -1) {
            Py_DECREF(deltas);
            return NULL;
        }
    }

    return deltas;
}

static PyMethodDef DeltaMethods[] = {
    {"generate_delta", generate_delta, METH_VARARGS, "Generate delta between two objects"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef deltamodule = {
    PyModuleDef_HEAD_INIT,
    "delta_utils_c",
    NULL,
    -1,
    DeltaMethods
};

PyMODINIT_FUNC PyInit_delta_utils_c(void) {
    return PyModule_Create(&deltamodule);
}
