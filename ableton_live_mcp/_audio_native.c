#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <math.h>
#include <stdint.h>
#include <string.h>

/* Fixed-size accumulators, immutable input, no Live API access. */
static int read_state(PyObject *obj, double *out, int channels) {
    PyObject *seq = PySequence_Fast(obj, "Accumulator must be a sequence");
    if (!seq) return 0;
    if (PySequence_Fast_GET_SIZE(seq) != channels) {
        Py_DECREF(seq);
        PyErr_SetString(PyExc_ValueError, "Accumulator channel count mismatch");
        return 0;
    }
    for (int i = 0; i < channels; i++) {
        out[i] = PyFloat_AsDouble(PySequence_Fast_GET_ITEM(seq, i));
        if (PyErr_Occurred()) { Py_DECREF(seq); return 0; }
    }
    Py_DECREF(seq);
    return 1;
}

static PyObject *pcm_accumulate(PyObject *self, PyObject *args) {
    PyObject *bytes, *states[4];
    int width, channels;
    double a[4][8];
    if (!PyArg_ParseTuple(args, "OiiOOOO", &bytes, &width, &channels,
                          &states[0], &states[1], &states[2], &states[3])) return NULL;
    if (!PyBytes_Check(bytes) || width < 1 || width > 4 || channels < 1 || channels > 8) {
        PyErr_SetString(PyExc_ValueError, "Expected PCM bytes, width 1–4, channels 1–8");
        return NULL;
    }
    Py_ssize_t size = PyBytes_GET_SIZE(bytes);
    if (size % (width * channels)) {
        PyErr_SetString(PyExc_ValueError, "Incomplete PCM frame");
        return NULL;
    }
    for (int j = 0; j < 4; j++) if (!read_state(states[j], a[j], channels)) return NULL;
    const unsigned char *data = (const unsigned char *)PyBytes_AS_STRING(bytes);
    int64_t scale = (int64_t)1 << (8 * width - 1);
    Py_BEGIN_ALLOW_THREADS
    for (Py_ssize_t offset = 0, sample = 0; offset < size; offset += width, sample++) {
        int channel = (int)(sample % channels);
        uint32_t raw = 0;
        for (int b = 0; b < width; b++) raw |= (uint32_t)data[offset + b] << (8 * b);
        int64_t integer = width == 1 ? (int64_t)raw - 128 :
            ((raw & (uint32_t)scale) ? (int64_t)raw - 2 * scale : (int64_t)raw);
        double value = (double)integer / (double)scale;
        double absolute = fabs(value);
        if (absolute > a[0][channel]) a[0][channel] = absolute;
        a[1][channel] += value * value;
        a[2][channel] += value;
        a[3][channel] += (integer <= -scale || integer >= scale - 1);
    }
    Py_END_ALLOW_THREADS
    PyObject *result = PyTuple_New(4);
    if (!result) return NULL;
    for (int j = 0; j < 4; j++) {
        PyObject *list = PyList_New(channels);
        if (!list) { Py_DECREF(result); return NULL; }
        for (int c = 0; c < channels; c++) {
            PyObject *value = PyFloat_FromDouble(a[j][c]);
            if (!value) { Py_DECREF(list); Py_DECREF(result); return NULL; }
            PyList_SET_ITEM(list, c, value);
        }
        PyTuple_SET_ITEM(result, j, list);
    }
    return result;
}

static void compensated_add(double value, double *sum, double *correction) {
    double total = *sum + value;
    *correction += fabs(*sum) >= fabs(value) ? (*sum - total) + value : (value - total) + *sum;
    *sum = total;
}

static PyObject *float_stats(PyObject *self, PyObject *args) {
    PyObject *bytes;
    Py_ssize_t count;
    if (!PyArg_ParseTuple(args, "On", &bytes, &count)) return NULL;
    if (!PyBytes_Check(bytes) || count < 0 || count > PyBytes_GET_SIZE(bytes) / 8) {
        PyErr_SetString(PyExc_ValueError, "Expected native-endian float64 bytes and a valid count");
        return NULL;
    }
    const char *data = PyBytes_AS_STRING(bytes);
    double peak = 0, sum = 0, square = 0, sum_error = 0, square_error = 0;
    Py_ssize_t full_scale = 0;
    int invalid = 0;
    Py_BEGIN_ALLOW_THREADS
    for (Py_ssize_t i = 0; i < count; i++) {
        double value;
        memcpy(&value, data + i * 8, 8);
        double squared = value * value;
        if (!isfinite(value) || !isfinite(squared)) { invalid = 1; break; }
        double absolute = fabs(value);
        if (absolute > peak) peak = absolute;
        full_scale += absolute >= 1.0;
        compensated_add(value, &sum, &sum_error);
        compensated_add(squared, &square, &square_error);
    }
    Py_END_ALLOW_THREADS
    if (invalid || !isfinite(sum + sum_error) || !isfinite(square + square_error)) {
        PyErr_SetString(PyExc_ValueError, "Audio contains NaN or infinite samples, or overflowing energy");
        return NULL;
    }
    return Py_BuildValue("dddn", peak, square + square_error, sum + sum_error, full_scale);
}

static PyMethodDef methods[] = {
    {"pcm_accumulate", pcm_accumulate, METH_VARARGS, "Accumulate interleaved integer PCM."},
    {"float_stats", float_stats, METH_VARARGS, "Measure one float64 channel with compensated sums."},
    {NULL, NULL, 0, NULL}
};
static struct PyModuleDef module = {PyModuleDef_HEAD_INIT, "_audio_native", NULL, -1, methods};
PyMODINIT_FUNC PyInit__audio_native(void) { return PyModule_Create(&module); }
