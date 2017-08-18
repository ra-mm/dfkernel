define([
    'notebook/js/outputarea',
    'jquery',
    'base/js/utils',
    'base/js/security',
    'base/js/keyboard',
    'services/config',
    'notebook/js/mathjaxutils',
    'components/marked/lib/marked',
], function(outputarea, $, utils, security, keyboard, configmod, mathjaxutils, marked) {
    "use strict";

    var OutputArea = outputarea.OutputArea;

    // FIXME pull these in instead?
    // Declare mime type as constants
    var MIME_JAVASCRIPT = 'application/javascript';
    var MIME_HTML = 'text/html';
    var MIME_MARKDOWN = 'text/markdown';
    var MIME_LATEX = 'text/latex';
    var MIME_SVG = 'image/svg+xml';
    var MIME_PNG = 'image/png';
    var MIME_JPEG = 'image/jpeg';
    var MIME_PDF = 'application/pdf';
    var MIME_TEXT = 'text/plain';

    OutputArea.prototype.handle_output = function(msg, cell_tag) {
        var json = {};
        var msg_type = json.output_type = msg.header.msg_type;
        var content = msg.content;
        switch(msg_type) {
        case "stream" :
            json.text = content.text;
            json.name = content.name;
            break;
        case "execute_result":
            json.execution_count = content.execution_count;
            json.cell_tag = cell_tag;
        case "update_display_data":
        case "display_data":
            json.transient = content.transient;
            json.data = content.data;
            json.metadata = content.metadata;
            break;
        case "error":
            json.ename = content.ename;
            json.evalue = content.evalue;
            json.traceback = content.traceback;
            break;
        default:
            console.error("unhandled output message", msg);
            return;
        }
        this.append_output(json);
    };

    OutputArea.prototype.append_execute_result = function (json) {
        var n = json.execution_count || ' ';
        var toinsert = this.create_output_area();
        this._record_display_id(json, toinsert);
        if (this.prompt_area) {
            toinsert.find('div.prompt')
                    .addClass('output_prompt')
                    .empty()
                    .append(
                      $('<bdi>').text('Out')
                    ).append(
                      '[' + n + ']<br>.' + json.cell_tag + ':'
                    );
        }
        var inserted = this.append_mime_type(json, toinsert);
        if (inserted) {
            inserted.addClass('output_result');
        }
        this._safe_append(toinsert);
        // If we just output latex, typeset it.
        if ((json.data[MIME_LATEX] !== undefined) ||
            (json.data[MIME_HTML] !== undefined) ||
            (json.data[MIME_MARKDOWN] !== undefined)) {
            this.typeset();
        }
    };

});
