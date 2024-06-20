import asyncio
from functools import partial
import ipykernel.ipkernel
from ipykernel.ipkernel import *
from tokenize import TokenError

"""The IPython kernel implementation"""

import ast
import sys
import time
import inspect

from traitlets import Type
from ipykernel.jsonutil import json_clean

from ipykernel.comm import Comm
from IPython import get_ipython

try:
    from IPython.core.interactiveshell import _asyncio_runner
except ImportError:
    _asyncio_runner = None

from .zmqshell import ZMQInteractiveShell
from .utils import (
    ground_refs,
    convert_dollar,
    convert_identifier,
    ref_replacer,
    identifier_replacer,
    dollar_replacer,
    get_references,
    compare_code_cells
)


def _accepts_cell_id(meth):
    parameters = inspect.signature(meth).parameters
    cid_param = parameters.get("cell_id")
    return (cid_param and cid_param.kind == cid_param.KEYWORD_ONLY) or any(
        p.kind == p.VAR_KEYWORD for p in parameters.values()
    )


class IPythonKernel(ipykernel.ipkernel.IPythonKernel):
    shell_class = Type(ZMQInteractiveShell)
    execution_count = None

    def __init__(self, **kwargs):
        super(IPythonKernel, self).__init__(**kwargs)
        self.shell.displayhook.get_execution_count = lambda: int(
            self.execution_count, 16
        )
        self.shell.display_pub.get_execution_count = lambda: int(
            self.execution_count, 16
        )
        # self.dfcode = Comm(target_name='dfcode',data={})#get_ipython().kernel.comm_manager.register_target('dfcode', self.dfcode_comm)

        # # first use nest_ayncio for nested async, then add asyncio.Future to tornado
        # nest_asyncio.apply()
        # # from maartenbreddels: https://github.com/jupyter/nbclient/pull/71/files/a79ae70eeccf1ab8bdd28370cd28f9546bd4f657
        # # If tornado is imported, add the patched asyncio.Future to its tuple of acceptable Futures"""
        # # original from vaex/asyncio.py
        # if 'tornado' in sys.modules:
        #     import tornado.concurrent
        #     if asyncio.Future not in tornado.concurrent.FUTURES:
        #         tornado.concurrent.FUTURES = tornado.concurrent.FUTURES + (
        #         asyncio.Future,)

    @property
    def execution_count(self):
        # return self.shell.execution_count
        return self.shell.uuid

    @execution_count.setter
    def execution_count(self, value):
        # Ignore the incrememnting done by KernelBase, in favour of our shell's
        # execution counter.
        pass

    # def _publish_execute_input(self, code, parent, execution_count):
    #     # go through nodes and for each node that exists in self.shell's history
    #     # as a load, change it to a var$ce1 reference
    #     # FIXME deal with scoping
    #
    #     super()._publish_execute_input(code, parent, execution_count)

    # def dfcode_comm(self, comm, msg, ui_code={}):
    #     self.log.warn('___hit on dfcode_comm_____')
    #     comm.send({'code_dict': ui_code})

    #     @comm.on_msg
    #     def _recv(msg):
    #         pass

    async def execute_request(self, stream, ident, parent):
        """handle an execute_request"""
        try:
            content = parent["content"]
            code = content["code"]
            silent = content["silent"]
            store_history = content.get("store_history", not silent)
            user_expressions = content.get("user_expressions", {})
            allow_stdin = content.get("allow_stdin", False)
        except:
            self.log.error("Got bad msg: ")
            self.log.error("%s", parent)
            return

        stop_on_error = content.get("stop_on_error", True)

        # grab and remove dfkernel_data from user_expressions
        # there just for convenience of not modifying the msg protocol
        dfkernel_data = user_expressions.pop("__dfkernel_data__", {})

        input_tags = dfkernel_data.get("input_tags", {})
        # print("SETTING INPUT TAGS:", input_tags, file=sys.__stdout__)

        #output tags {output_tag: (exported_Cell_id)}
        self.log.warn(f'OUTPUT TAGS: {dfkernel_data['output_tags']}')
        output_tags_links = {}
        for op_id, op_tags in dfkernel_data['output_tags'].items():
            for tag in op_tags:
                output_tags_links.setdefault(tag, set()).add(op_id)
        
        #self.log.warn(f'BEFORE OUTPUTTAGS_LINKS:  {output_tags_links}')

        self._ref_links = output_tags_links
        self.shell.input_tags = input_tags

        self._outer_stream = stream
        self._outer_ident = ident
        self._outer_parent = parent
        self._outer_stop_on_error = stop_on_error
        self._outer_allow_stdin = allow_stdin
        self._outer_dfkernel_data = dfkernel_data
        self._identifier_refs = {}
        self._persistent_code = {}

        res = await self.inner_execute_request(
            code,
            dfkernel_data.get("uuid"),
            silent,
            store_history,
            user_expressions,
        )

        # self._outer_stream = None
        # self._outer_ident = None
        # self._outer_parent = None
        # self._outer_stop_on_error = None
        # self._outer_allow_stdin = None
        # self._outer_dfkernel_data = None

        if res.success:
            # self.log.warn('__________xxxxxxxxxxxxxxxxxxxxxxxSTARTxxxxxxxxxxxxxxxxxxxxxxxxx___')
            # self.log.warn(f'RFERECNCES OF CELL:{self._identifier_refs}\n\n')
            # self.log.warn(f'output_tags_links:{output_tags_links}\ndfkernel_data:{dfkernel_data['all_refs']}\n')
            cells_to_update, cells_to_execute, dfkernel_data = await self.update_identifiers(output_tags_links, dfkernel_data, input_tags=input_tags)
            self.log.warn(f"*******\nUI CODE TO UPDATE : {cells_to_update}\n CELLS TO EXECUTE: {cells_to_execute}\n**********")
            self._outer_dfkernel_data = dfkernel_data
            code_to_update = dict()
            for id in cells_to_update+cells_to_execute:
                code_to_update[id] = dfkernel_data["code_dict"][id]
            dfcode = Comm(target_name='dfcode',data={})
            dfcode.open()
            dfcode.send({'code_dict': code_to_update}) 
            

            # for cell_uuid in updated_cells:
            #     res = await self.inner_execute_request(
            #     dfkernel_data["code_dict"][cell_uuid],
            #     cell_uuid,
            #     silent,
            #     store_history,
            #     user_expressions)
        
        # self.log.warn('__________xxxxxxxxxxxxxxxxxxxxxxxENDxxxxxxxxxxxxxxxxxxxxxxxxx___')

        # output_tags_links = {}
        # for op_id, op_tags in dfkernel_data['output_tags'].items():
        #     for tag in op_tags:
        #         output_tags_links.setdefault(tag, set()).add(op_id)
        # self.log.warn(f'AFTER OUTPUTTAGS_LINKS:  {output_tags_links}')
        # print("LINKS:",self.shell.dataflow_state.links)
        #tags exported second time
        

    async def inner_execute_request(
        self, code, uuid, silent, store_history=True, user_expressions=None
    ):
        stream = self._outer_stream
        ident = self._outer_ident
        parent = self._outer_parent
        stop_on_error = self._outer_stop_on_error
        allow_stdin = self._outer_allow_stdin
        dfkernel_data = self._outer_dfkernel_data

        input_tags = dfkernel_data.get("input_tags", {})

        # FIXME does it make sense to reparent a request?
        metadata = self.init_metadata(parent)

        # print("INNER EXECUTE:", uuid)

        try:
            execution_count = int(uuid, 16)
        except:
            # FIXME for debugging
            uuid = "1"
            execution_count = 1
        dollar_converted = False
        orig_code = code
        parsed_code = ''
        try:
            code = convert_dollar(
                code, self.shell.dataflow_state, uuid, identifier_replacer, input_tags
            )
            # self.log.warn(f'code after Dollar Convert :\n{code}')
            # self.log.warn('_________________________________________________')
            dollar_converted = True
            code = ground_refs(
                code, self.shell.dataflow_state, uuid, identifier_replacer, input_tags, all_refs=self._ref_links
            )
            # self.log.warn(f'code after ground ref :\n{code}')
            # self.log.warn('_________________________________________________')
            parsed_code = code
            self._identifier_refs[uuid] = get_references(code)
            #self.log.warn(f"DISPLAYed Code:\n{code}\nCell references of {uuid}: {r}")
            # code, self._identifier_refs = convert_identifier(code, dollar_replacer, uuid=uuid, identifier_refs = self._identifier_refs)
            code = convert_identifier(code, dollar_replacer)
            # self.log.warn(f'code after convert identifier :\n{code}')
            # self.log.warn('_________________________________________________')
            dollar_converted = False
        except SyntaxError as e:
            # ignore this for now, catch it in do_execute
            # print(e)
            if dollar_converted:
                code = orig_code
            pass
        except TokenError as e:
            # ignore this for now, catch it in do_execute
            pass

        #print("FIRST CODE:", code)
        if not silent:
            if len(parsed_code) > 0:
                #r = get_references(parsed_code)
                self.log.warn(f"___ref_links__ = {self._ref_links}")
                display_code = convert_identifier(parsed_code, dollar_replacer, ref_links=self._ref_links, retain_ids=False)
                # self.log.warn(f"DISPLAYED Code:\n{display_code}\nCell references of {uuid}: {r}")
                self._publish_execute_input(display_code, parent, execution_count)
            else:
                self._publish_execute_input(code, parent, execution_count)

        # update the code_dict with the modified code
        dfkernel_data["code_dict"][uuid] = code
        # convert all tilded code
        try:
            code = convert_dollar(
                code, self.shell.dataflow_state, uuid, ref_replacer, input_tags
            )
            self._persistent_code[uuid] = code
        except SyntaxError as e:
            # ignore this for now, catch it in do_execute
            pass
        except TokenError as e:
            # ignore this for now, catch it in do_execute
            pass

        # print("SECOND CODE:", code)

        cell_id = (parent.get("metadata") or {}).get("cellId")
        if _accepts_cell_id(self.do_execute):
            reply_content = self.do_execute(
                code,
                uuid,
                dfkernel_data,
                silent,
                store_history,
                user_expressions,
                allow_stdin,
                cell_id=cell_id,
            )
        else:
            reply_content = self.do_execute(
                code,
                uuid,
                dfkernel_data,
                silent,
                store_history,
                user_expressions,
                allow_stdin,
            )

        if inspect.isawaitable(reply_content):
            reply_content = await reply_content

        # need to unpack
        reply_content, res = reply_content

        # Flush output before sending the reply.
        sys.stdout.flush()
        sys.stderr.flush()
        # FIXME: on rare occasions, the flush doesn't seem to make it to the
        # clients... This seems to mitigate the problem, but we definitely need
        # to better understand what's going on.
        if self._execute_sleep:
            time.sleep(self._execute_sleep)

        # Send the reply.
        reply_content = json_clean(reply_content)
        metadata = self.finish_metadata(parent, metadata, reply_content)

        reply_msg = self.session.send(
            stream,
            "execute_reply",
            reply_content,
            parent,
            metadata=metadata,
            ident=ident,
        )

        self.log.debug("%s", reply_msg)

        if not silent and reply_msg["content"]["status"] == "error" and stop_on_error:
            self._abort_queues()

        return res

    async def do_execute(
        self,
        code,
        uuid,
        dfkernel_data,
        silent,
        store_history=True,
        user_expressions=None,
        allow_stdin=False,
        *,
        cell_id=None,
    ):
        shell = self.shell  # we'll need this a lot here

        self._forward_input(allow_stdin)

        # print("DO EXECUTE:", uuid, file=sys.__stdout__)
        reply_content = {}
        if hasattr(shell, "run_cell_async") and hasattr(shell, "should_run_async"):
            run_cell = partial(
                shell.run_cell_async_override, uuid=uuid, dfkernel_data=dfkernel_data
            )
            should_run_async = shell.should_run_async
            with_cell_id = _accepts_cell_id(run_cell)
        else:
            should_run_async = lambda cell: False

            # older IPython,
            # use blocking run_cell and wrap it in coroutine
            async def run_cell(*args, **kwargs):
                kwargs["uuid"] = uuid
                kwargs["dfkernel_data"] = dfkernel_data
                return shell.run_cell(*args, **kwargs)

            with_cell_id = _accepts_cell_id(shell.run_cell)

        res = None
        try:
            # default case: runner is asyncio and asyncio is already running
            # TODO: this should check every case for "are we inside the runner",
            # not just asyncio
            preprocessing_exc_tuple = None
            try:
                transformed_cell = self.shell.transform_cell(code)
            except Exception:
                transformed_cell = code
                preprocessing_exc_tuple = sys.exc_info()

            if (
                _asyncio_runner
                and shell.loop_runner is _asyncio_runner
                and asyncio.get_event_loop().is_running()
                and should_run_async(
                    code,
                    transformed_cell=transformed_cell,
                    preprocessing_exc_tuple=preprocessing_exc_tuple,
                )
            ):
                # print("RUNNING CELL ASYNC:", uuid, file=sys.__stdout__)
                if with_cell_id:
                    coro = run_cell(
                        code,
                        store_history=store_history,
                        silent=silent,
                        transformed_cell=transformed_cell,
                        preprocessing_exc_tuple=preprocessing_exc_tuple,
                        cell_id=cell_id,
                    )
                else:
                    coro = run_cell(
                        code,
                        store_history=store_history,
                        silent=silent,
                        transformed_cell=transformed_cell,
                        preprocessing_exc_tuple=preprocessing_exc_tuple,
                    )
                coro_future = asyncio.ensure_future(coro)

                with self._cancel_on_sigint(coro_future):
                    try:
                        # print("TRYING TO YIELD CORO_FUTURE")
                        res = await coro_future
                    finally:
                        shell.events.trigger("post_execute")
                        if not silent:
                            shell.events.trigger("post_run_cell", res)
            else:
                # runner isn't already running,
                # make synchronous call,
                # letting shell dispatch to loop runners
                if with_cell_id:
                    res = shell.run_cell(
                        code,
                        uuid=uuid,
                        dfkernel_data=dfkernel_data,
                        store_history=store_history,
                        silent=silent,
                        cell_id=cell_id,
                    )
                else:
                    res = shell.run_cell(
                        code,
                        uuid=uuid,
                        dfkernel_data=dfkernel_data,
                        store_history=store_history,
                        silent=silent,
                    )
        finally:
            self._restore_input()

        # print("GOT RES:", res)
        if res.error_before_exec is not None:
            err = res.error_before_exec
        else:
            err = res.error_in_exec

        # print("DELETED CELLS:", res, file=sys.__stdout__)
        if hasattr(res, "deleted_cells"):
            reply_content["deleted_cells"] = res.deleted_cells

        if res.success:
            # print("SETTING DEPS", res.all_upstream_deps, res.all_downstream_deps,file=sys.__stdout__)
            reply_content["status"] = "ok"
            #self._identifier_refs = identifier_refs
            #self.log.warn(f'IDENTIFIER REFS:   {self._identifier_refs}')

            if hasattr(res, "nodes"):
                reply_content["nodes"] = res.nodes
                reply_content["links"] = res.links
                reply_content["cells"] = res.cells
                self.log.warn(f'___{uuid}__:____{self._identifier_refs.get(uuid, {})}______{code}__________________')
                reply_content["identifier_refs"] = self._identifier_refs
                reply_content["persistent_code"] = self._persistent_code

                reply_content["upstream_deps"] = res.all_upstream_deps
                reply_content["downstream_deps"] = res.all_downstream_deps
                reply_content["imm_upstream_deps"] = res.imm_upstream_deps
                reply_content["imm_downstream_deps"] = res.imm_downstream_deps
                reply_content["update_downstreams"] = res.update_downstreams
                reply_content["internal_nodes"] = res.internal_nodes
        else:
            reply_content["status"] = "error"

            reply_content.update(
                {
                    "traceback": shell._last_traceback or [],
                    "ename": str(type(err).__name__),
                    "evalue": str(err),
                }
            )

            # FIXME: deprecated piece for ipyparallel (remove in 5.0):
            e_info = dict(
                engine_uuid=self.ident, engine_id=self.int_id, method="execute"
            )
            reply_content["engine_info"] = e_info

        # Return the execution counter so clients can display prompts
        reply_content["execution_count"] = int(uuid, 16)
        # reply_content['execution_count'] = shell.execution_count - 1

        if "traceback" in reply_content:
            self.log.info(
                "Exception in execute request:\n%s",
                "\n".join(reply_content["traceback"]),
            )

        # At this point, we can tell whether the main code execution succeeded
        # or not.  If it did, we proceed to evaluate user_expressions
        if reply_content["status"] == "ok":
            reply_content["user_expressions"] = shell.user_expressions(
                user_expressions or {}
            )
        else:
            # If there was an error, don't even try to compute expressions
            reply_content["user_expressions"] = {}

        # Payloads should be retrieved regardless of outcome, so we can both
        # recover partial output (that could have been generated early in a
        # block, before an error) and always clear the payload system.
        reply_content["payload"] = shell.payload_manager.read_payload()
        # Be aggressive about clearing the payload because we don't want
        # it to sit in memory until the next execute_request comes in.
        shell.payload_manager.clear_payload()

        return reply_content, res

    async def update_identifiers(self, output_tags_links, dfkernel_data, input_tags={}):
        '''to_update_links: 
            value = variable exported for second time 
            key = uuid of the cell where the varibale is exported for first time
        '''
        #self._ref_links - output tag : {uuid1, uuid2, ...}

        to_update_links = {} 
        for op_tag, op_ids in output_tags_links.items():
            if (op_tag in self.shell.dataflow_state.links and len(op_ids) == 1
                and len(set(self.shell.dataflow_state.links[op_tag]) - op_ids) == 1):
                to_update_links.setdefault(next(iter(op_ids)), set()).add(op_tag)
                self._ref_links[op_tag].update(set(self.shell.dataflow_state.links[op_tag]) - op_ids)

        #delete it
        if len(to_update_links) > 0:
            # self.log.warn("LINKS BEFORE EXECUTING THE CELL")
            # self.log.warn(output_tags_links)
            self.log.warn("VARIBALES EXPORTED SECOND TIME ARE:")
            self.log.warn(to_update_links)
            
        #   self.log.warn('__________________________')
            # self.log.warn("EXISTING VARIABLES ALL REFS BEFORE RUNNING THE CELL:")
            # self.log.warn(dfkernel_data['all_refs'])
            # self.log.warn('__________________________')

        '''
        Combine self._identifier_refs and dfkernel_data['all_refs']
        Updated refs are available in self._identifier_refs
        All refs are available in dfkernel_data['all_refs']
        '''
        for cell_id, cell_ref_tags in dfkernel_data['all_refs'].items():
            if not self._identifier_refs.get(cell_id):
                self._identifier_refs[cell_id] = cell_ref_tags

        impacted_cells= set()
        reversion_links = {} # In key:values,  length of values be one always ??
        for op_id, op_tags in to_update_links.items():
            for ref_id, ref_tags in self._identifier_refs.items():
                if op_id in ref_tags and len(op_tags&set(ref_tags[op_id])) > 0:
                    impacted_cells.add(ref_id)
                    for tags_to_update in op_tags&set(ref_tags[op_id]):
                        reversion_links.setdefault(tags_to_update, set()).add(op_id)

        # self.log.warn(f'Updating the code in cells......{update_cells}......')
        
        '''
        Add current UUID only when it uses same identifier for export and reference 
        eg: g = g+10, here g is refered and g is exported
        '''
        if self._identifier_refs and len(self._identifier_refs.get(dfkernel_data.get("uuid"))) > 0:
            curr_tags_exported = set(reversion_links.keys())
            curr_tags_ref = set()
            for cell_id, cell_ref_tags in self._identifier_refs[dfkernel_data.get("uuid")].items():
                curr_tags_ref.update(cell_ref_tags)
            if len(curr_tags_ref&curr_tags_exported) > 0:
                impacted_cells.add(dfkernel_data.get("uuid"))

        # '''
        # recent executed status may change if the last cell is not executed again.
        # '''
        # if len(impacted_cells) > 0:
        #     impacted_cells.add(dfkernel_data.get("uuid"))

        ''''
        Note: Reason for repeatation of code: on execution dependent cells may get updated with latest exported tag values
        '''
        display_code = dict()
        parsed_code = dict()
        cells_to_execute = list()
        cells_to_update_code = list()
        for cell_uuid in impacted_cells:
            code = dfkernel_data["code_dict"][cell_uuid]
            try:
                #Display code is generated in below block
                code = convert_dollar(code, self.shell.dataflow_state, cell_uuid, identifier_replacer, input_tags)
                code = ground_refs(code, self.shell.dataflow_state, cell_uuid, identifier_replacer, input_tags, all_refs=self._ref_links)
                code = convert_identifier(code, dollar_replacer, ref_links=self._ref_links, reversion_links=reversion_links, retain_ids=True)
                display_code[cell_uuid] = code
                dfkernel_data["code_dict"][cell_uuid] = code

                #using display code persistent code is generated in below block
                code = convert_dollar(code, self.shell.dataflow_state, cell_uuid, identifier_replacer, input_tags)
                code = ground_refs(code, self.shell.dataflow_state, cell_uuid, identifier_replacer, input_tags, all_refs=self._ref_links)
                code = convert_identifier(code, dollar_replacer)
                code = convert_dollar(code, self.shell.dataflow_state, cell_uuid, ref_replacer, input_tags)
                parsed_code[cell_uuid] = code
            except SyntaxError as e:
                pass
            except TokenError as e:
                pass

            # self._publish_execute_input(code, parent, execution_count)
            #self.log.warn(f" CODED UPDATED as part of reversion::::::::{code}")
            

            #dfkernel_data["code_dict"][cell_uuid] = code
            self.log.warn('___________________________________________')
            self.log.warn(f'Displayed code: {display_code[cell_uuid]}')
            self.log.warn(f'Parsed code: {parsed_code[cell_uuid]}')
            if dfkernel_data['persisted_code'].get(cell_uuid):
                self.log.warn(f'Persisted code: {dfkernel_data['persisted_code'][cell_uuid]}')
            self.log.warn('___________________________________________')
            if dfkernel_data['persisted_code'].get(cell_uuid):
                if compare_code_cells(parsed_code[cell_uuid], dfkernel_data['persisted_code'][cell_uuid]):
                    cells_to_update_code.append(cell_uuid)
                else:
                    cells_to_execute.append(cell_uuid)
            else:
                cells_to_execute.append(cell_uuid)
                
        return cells_to_update_code, cells_to_execute, dfkernel_data 

# This exists only for backwards compatibility - use IPythonKernel instead
class Kernel(IPythonKernel):
    def __init__(self, *args, **kwargs):
        import warnings

        warnings.warn(
            "Kernel is a deprecated alias of dfkernel.ipkernel.IPythonKernel",
            DeprecationWarning,
        )
        super(Kernel, self).__init__(*args, **kwargs)
