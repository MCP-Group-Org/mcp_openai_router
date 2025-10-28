Критический анализ кода и варианты рефакторинга:

## Критические замечания

1. **Нарушение Single Responsibility Principle** - функция делает слишком много:
   - Валидация входных данных
   - Создание клиента
   - Формирование запросов
   - Обработка ответов
   - Полинг статуса
   - Обработка инструментов
   - Нормализация выходных данных

2. **Высокая сложность** - 200+ строк, глубокий уровень вложенности
3. **Смешение абстракций** - низкоуровневые сетевые вызовы и бизнес-логика
4. **Жесткая связь с OpenAI API** - несмотря на декларацию изоляции
5. **Потенциальные проблемы с производительностью** - синхронный sleep в цикле опроса

## Предлагаемый рефакторинг

### 1. Разделение на классы с четкими ответственностями

```python
class OpenAIClientAdapter:
    """Адаптер для работы с OpenAI Responses API"""
    
    def __init__(self, client_factory=None):
        self._client_factory = client_factory or _create_openai_client
        self._client = None
    
    def initialize(self):
        """Ленивая инициализация клиента"""
        if self._client is None:
            self._client = self._client_factory()
            self._validate_client_capabilities()
    
    def _validate_client_capabilities(self):
        """Проверка возможностей клиента"""
        responses_api = getattr(self._client, "responses", None)
        if responses_api is None:
            raise RuntimeError("OpenAI client missing Responses API")
        
        if not callable(getattr(responses_api, "create", None)):
            raise RuntimeError("OpenAI client does not expose responses.create")
    
    def create_response(self, payload):
        """Создание нового ответа"""
        self.initialize()
        return self._client.responses.create(**payload)
    
    def retrieve_response(self, response_id):
        """Получение статуса ответа"""
        self.initialize()
        retrieve_fn = getattr(self._client.responses, "retrieve", None)
        if not callable(retrieve_fn):
            return None
        
        try:
            return retrieve_fn(response_id=response_id)
        except TypeError:
            return retrieve_fn(id=response_id)

class ResponsePoller:
    """Сервис для опроса статуса ответов"""
    
    def __init__(self, client_adapter, poll_delay=POLL_DELAY, max_polls=MAX_POLLS, semaphore=POLL_SEM):
        self.client_adapter = client_adapter
        self.poll_delay = poll_delay
        self.max_polls = max_polls
        self.semaphore = semaphore
    
    def poll_until_complete(self, response_id, initial_data=None):
        """Ожидание завершения обработки ответа"""
        if not self.semaphore.acquire(timeout=5.0):
            logger.warning("Poll semaphore timeout for %s", response_id)
            return initial_data or {}
        
        try:
            return self._do_polling(response_id, initial_data)
        finally:
            self.semaphore.release()
    
    def _do_polling(self, response_id, initial_data):
        data = initial_data or {}
        status = data.get("status")
        
        # Если уже терминальный статус
        if status and status not in {"queued", "in_progress"}:
            return data
        
        polls = 0
        t_start = time.time()
        
        while polls < self.max_polls:
            poll_data = self._single_poll(response_id, polls)
            if not poll_data:
                break
            
            status = poll_data.get("status")
            if status and status not in {"queued", "in_progress"}:
                total_ms = (time.time() - t_start) * 1000.0
                logger.info("Response completed in %.1f ms after %d polls", total_ms, polls + 1)
                return poll_data
            
            polls += 1
            time.sleep(self.poll_delay)
        
        logger.info("Poll limit reached after %d polls", polls)
        return data
    
    def _single_poll(self, response_id, poll_count):
        t0 = time.time()
        try:
            retrieved = self.client_adapter.retrieve_response(response_id)
            dt = (time.time() - t0) * 1000.0
            logger.debug("Poll %d completed in %.1f ms", poll_count + 1, dt)
            return _maybe_model_dump(retrieved) if retrieved else None
        except Exception as exc:
            logger.warning("Poll %d failed: %s", poll_count + 1, exc)
            return None

class ThinkToolProcessor:
    """Обработчик think-инструмента"""
    
    def process(self, tool_calls):
        """Обработка набора think-вызовов"""
        follow_up_inputs = []
        think_logs = []
        remaining_calls = []
        
        for call in tool_calls:
            if call.get("toolName") != "think":
                remaining_calls.append(call)
                continue
            
            result = self._process_single_think(call)
            think_logs.append(result.log_entry)
            
            if result.is_error:
                raise ThinkToolError(result.error_message, result.metadata)
            
            follow_up_inputs.append(result.follow_up_input)
        
        return ThinkToolResult(
            follow_up_inputs=follow_up_inputs,
            think_logs=think_logs,
            remaining_calls=remaining_calls
        )
    
    def _process_single_think(self, call):
        """Обработка одного think-вызова"""
        call_id = call.get("id")
        arguments = call.get("arguments") or {}
        
        if not isinstance(arguments, dict):
            arguments = {"raw": arguments}
        
        think_result = _handle_think(arguments)
        log_entry = {
            "callId": call_id,
            "status": "error" if think_result.get("isError") else "ok",
            "result": think_result
        }
        
        if think_result.get("isError"):
            error_message = self._extract_error_message(think_result)
            return ThinkCallResult.error(log_entry, error_message, think_result.get("metadata"))
        
        if not isinstance(call_id, str) or not call_id:
            return ThinkCallResult.error(log_entry, "Invalid think-tool call identifier")
        
        follow_up_input = {
            "type": "function_call_output",
            "call_id": call_id,
            "output": [{
                "type": "input_text",
                "text": self._convert_think_content(think_result.get("content"))
            }]
        }
        
        return ThinkCallResult.success(log_entry, follow_up_input)
    
    def _extract_error_message(self, think_result):
        """Извлечение сообщения об ошибке из результата think"""
        error_blocks = think_result.get("content") or [{"type": "text", "text": "think-tool returned error"}]
        error_texts = [
            block["text"] for block in error_blocks 
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ]
        return "\n".join(error_texts) or "think-tool returned error"
    
    def _convert_think_content(self, blocks):
        """Конвертация контента think в текст"""
        if not blocks:
            return "ok"
        
        texts = [
            block.get("text", "").strip() for block in blocks 
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ]
        return "\n\n".join(filter(None, texts))

class ChatResponseProcessor:
    """Основной процессор chat-ответов"""
    
    def __init__(self, client_adapter, poller, think_processor, max_turns=15):
        self.client_adapter = client_adapter
        self.poller = poller
        self.think_processor = think_processor
        self.max_turns = max_turns
    
    def process(self, initial_response_data, params):
        """Основной цикл обработки ответа"""
        current_data = self._resolve_initial_response(initial_response_data)
        final_meta = None
        think_logs = []
        
        for turn in range(self.max_turns):
            content_blocks, tool_calls, meta = self._extract_response_data(current_data)
            
            if meta:
                final_meta = meta
            
            if not tool_calls:
                return ProcessingResult.completed(
                    content=content_blocks,
                    metadata=final_meta,
                    think_logs=think_logs
                )
            
            try:
                think_result = self.think_processor.process(tool_calls)
                think_logs.extend(think_result.think_logs)
                
                if not think_result.follow_up_inputs:
                    return ProcessingResult.completed(
                        content=content_blocks,
                        metadata=final_meta,
                        think_logs=think_logs,
                        remaining_calls=think_result.remaining_calls
                    )
                
                current_data = self._send_follow_up(
                    think_result.follow_up_inputs,
                    current_data,
                    meta or final_meta,
                    params
                )
                
            except ThinkToolError as exc:
                return ProcessingResult.think_error(exc.message, exc.metadata, think_logs)
        
        return ProcessingResult.max_turns_exceeded(think_logs)
    
    def _resolve_initial_response(self, response_data):
        """Разрешение начального ответа (с опросом при необходимости)"""
        status = response_data.get("status")
        response_id = response_data.get("id")
        
        if not response_id:
            return response_data
        
        if status in {"queued", "in_progress"} or (status is None and self.poller.client_adapter.retrieve_response):
            return self.poller.poll_until_complete(response_id, response_data)
        
        return response_data
    
    def _extract_response_data(self, response_data):
        """Извлечение и нормализация данных ответа"""
        # Попробовать разные форматы нормализации
        content_blocks, tool_calls, meta = normalise_responses_output(response_data)
        if not content_blocks and not tool_calls:
            content_blocks, tool_calls, meta = normalise_chat_completion(response_data)
        if not content_blocks and not tool_calls and response_data:
            content_blocks = [{"type": "text", "text": json.dumps(response_data)}]
        
        if tool_calls:
            logger.info("Received tool calls: %s", tool_calls)
        
        return content_blocks, tool_calls, meta
    
    def _send_follow_up(self, follow_up_inputs, current_data, meta, params):
        """Отправка follow-up запроса"""
        response_id = self._get_response_id(current_data, meta)
        if not response_id:
            raise RuntimeError("Cannot send follow-up: missing response ID")
        
        follow_up_payload = {
            "model": params["model"],
            "previous_response_id": response_id,
            "input": follow_up_inputs,
        }
        
        if params.get("metadata"):
            follow_up_payload["metadata"] = params["metadata"]
        
        logger.info("Sending OpenAI follow-up: %s", follow_up_payload)
        t0 = time.time()
        
        try:
            follow_up_response = self.client_adapter.create_response(follow_up_payload)
            dt = (time.time() - t0) * 1000.0
            logger.info("Follow-up completed in %.1f ms", dt)
            
            follow_up_data = _maybe_model_dump(follow_up_response)
            return self._resolve_initial_response(follow_up_data)
            
        except Exception as exc:
            logger.exception("OpenAI follow-up call failed")
            raise RuntimeError(f"OpenAI follow-up call failed: {exc}")
    
    def _get_response_id(self, current_data, meta):
        """Извлечение ID ответа из различных источников"""
        return (
            (meta or {}).get("responseId") or
            current_data.get("id")
        )
```

### 2. Упрощенная основная функция

```python
def _handle_chat(arguments: Dict[str, Any]) -> ToolResponse:
    # 1) Валидация аргументов
    try:
        params = extract_chat_params(arguments)
        input_messages = normalize_input_messages(params["messages"])
    except ChatArgError as exc:
        return _tool_error(str(exc))
    
    # 2) Инициализация сервисов
    try:
        client_adapter = OpenAIClientAdapter()
        poller = ResponsePoller(client_adapter)
        think_processor = ThinkToolProcessor()
        processor = ChatResponseProcessor(client_adapter, poller, think_processor)
    except RuntimeError as exc:
        return _tool_error(str(exc))
    
    # 3) Подготовка и отправка начального запроса
    request_payload = build_request_payload(
        params, input_messages, 
        ensure_think_tool=THINK_TOOL_CONFIG.enabled
    )
    
    try:
        initial_response = client_adapter.create_response(request_payload)
        response_data = _maybe_model_dump(initial_response)
        logger.info("Initial request completed for model %s", params["model"])
    except Exception as exc:
        logger.exception("OpenAI API call failed")
        return _tool_error(f"OpenAI call failed: {exc}")
    
    # 4) Обработка ответа
    try:
        result = processor.process(response_data, params)
        return result.to_tool_response()
    except Exception as exc:
        logger.exception("Response processing failed")
        return _tool_error(f"Response processing failed: {exc}")
```

### 3. Data-классы для типизации

```python
@dataclass
class ThinkCallResult:
    log_entry: Dict[str, Any]
    follow_up_input: Optional[Dict[str, Any]] = None
    is_error: bool = False
    error_message: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    
    @classmethod
    def success(cls, log_entry, follow_up_input):
        return cls(log_entry=log_entry, follow_up_input=follow_up_input, is_error=False)
    
    @classmethod
    def error(cls, log_entry, error_message, metadata=None):
        return cls(
            log_entry=log_entry, 
            is_error=True, 
            error_message=error_message, 
            metadata=metadata
        )

@dataclass
class ThinkToolResult:
    follow_up_inputs: List[Dict[str, Any]]
    think_logs: List[Dict[str, Any]]
    remaining_calls: List[Dict[str, Any]]

@dataclass
class ProcessingResult:
    content: List[Dict[str, Any]]
    tool_calls: List[Dict[str, Any]]
    metadata: Optional[Dict[str, Any]]
    think_logs: List[Dict[str, Any]]
    is_error: bool = False
    error_message: Optional[str] = None
    
    @classmethod
    def completed(cls, content, metadata, think_logs, remaining_calls=None):
        return cls(
            content=content,
            tool_calls=remaining_calls or [],
            metadata=metadata,
            think_logs=think_logs
        )
    
    @classmethod
    def think_error(cls, error_message, metadata, think_logs):
        return cls(
            content=[],
            tool_calls=[],
            metadata=metadata,
            think_logs=think_logs,
            is_error=True,
            error_message=error_message
        )
    
    @classmethod
    def max_turns_exceeded(cls, think_logs):
        return cls(
            content=[],
            tool_calls=[],
            metadata=None,
            think_logs=think_logs,
            is_error=True,
            error_message="Reached maximum tool iterations without completion."
        )
    
    def to_tool_response(self):
        if self.is_error:
            return _tool_error(self.error_message, metadata=self.metadata)
        
        result = _tool_ok(
            content=self.content, 
            tool_calls=self.tool_calls, 
            metadata=self.metadata
        )
        
        if self.think_logs:
            metadata = result.setdefault("metadata", {})
            metadata["thinkTool"] = [
                {
                    "callId": log.get("callId"),
                    "status": log.get("status"),
                    "content": log.get("result", {}).get("content"),
                    "metadata": log.get("result", {}).get("metadata"),
                }
                for log in self.think_logs
            ]
        
        return result
```

## Преимущества рефакторинга

1. **Улучшенная поддерживаемость** - каждый класс имеет четкую зону ответственности
2. **Тестируемость** - каждый компонент можно тестировать изолированно
3. **Переиспользование** - компоненты можно использовать в других контекстах
4. **Читаемость** - основной поток логики теперь занимает ~20 строк
5. **Расширяемость** - легко добавить поддержку новых инструментов или провайдеров
6. **Типизация** - data-классы делают контракты явными

Такой подход делает код более устойчивым к изменениям и облегчает дальнейшее развитие функциональности.