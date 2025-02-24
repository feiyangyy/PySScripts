import time
from loguru import logger
import xml.dom.minidom
from xml.dom.minidom import parse
from dataclasses import dataclass

@dataclass
class PyShellCommand:
  """_summary_

  Returns:
      _type_: _description_
  """
  cmd: bytes
  res_pattern: str
  timeout: int
  repeat_cnt:int
  delay:float = 0
  done_delay:float = 0
  
  @staticmethod
  def ParseCommands(scripts_file, **cfg):
    """_summary_
    Parse the given script file(xml format), get all the commands.
    Args:
        scripts_file (str): The script file, which locates in a disk. TODO: recieve a string-content instead of a file.

    Returns:
        list[PyShellCommand]: Commands
    """
    root=parse(scripts_file)
    scripts = root.getElementsByTagName('scripts')[0]
    cmd_table=dict()
    # ctx={"sip":sip, "bip":bip, "gw":gw}
    for child in scripts.childNodes:
      if child.nodeType == child.ELEMENT_NODE:  # Make sure it's a element node
        cmd_list = []
        child:xml.dom.minidom.Element
        for cmd in child.getElementsByTagName("cmd"):
          cmd_str = cmd.getAttribute("command")
          delay=0
          if cmd.hasAttribute("delay"):
            delay = float(cmd.getAttribute("delay"))
          done_delay=0
          if cmd.hasAttribute("post_delay"):
            done_delay = float(cmd.getAttribute("post_delay"))
          ctx_str = cmd.getAttribute("ctx").strip()
          if ctx_str:
            cfg_v = cfg[ctx_str]
            if type(cfg_v) == int:
              cfg_v = hex(cfg_v)[2:]
            cmd_str = cmd_str.format(cfg_v)
          ucmd = PyShellCommand(cmd=cmd_str.encode(), res_pattern= cmd.getAttribute('pattern'), 
                  timeout=int(cmd.getAttribute('timeout')), repeat_cnt=int(cmd.getAttribute('repeat')), delay=delay, done_delay=done_delay)
          cmd_list.append(ucmd)
        logger.info(f"Model {child.tagName} has {cmd_list.__len__()} scripts.")
        cmd_table[child.tagName] = cmd_list
    return cmd_table
    

class NvPUbootSerial(object):
  baudrate=115200
  promt="NVP-SS#"
  pattern = re.compile(r'NVP-SS#\s*')
  timeout = 45
  cmd_padding='\r'.encode()
  block_uboot_cmd_out=False
  
  def __init__(self) -> None:
    self.port_str = ""
    self.ser = None
    self.server = None
  
  def HandshakeWithUboot(self):
    logger.info("Ready to handshake with uboot")
    logger.info(f"We'll handshake with uboot, Timeout configure is {self.timeout}s")
    cmd = PyShellCommand("  ".encode(), "", self.timeout, -1, 0)
    result =self.ExecuteCmd(cmd, True)
    if not result:
      logger.error("Failed to handshake with uboot. please check board boot mode or reboot.")
      raise BurnError("Failed to handshake with uboot!")
    logger.info("Handshake with uboot success!")

  def SetNetWork(self, sip, bip, gw):
    cmd = PyShellCommand(f"setenv gatewayip {gw}".encode(), res_pattern="", timeout=1, repeat_cnt=1, delay=0)
    b0 = self.ExecuteCmd(cmd)
    cmd = PyShellCommand(f"setenv ipaddr {bip}".encode(), "", timeout=1, repeat_cnt=1, delay=0)
    b1 = self.ExecuteCmd(cmd)
    cmd = PyShellCommand(f"setenv serverip {sip}".encode(), "", timeout=1, repeat_cnt=1, delay=0)
    b2 = self.ExecuteCmd(cmd)
    if not (b0 and b1 and b2):
      raise BurnError("Configure network failed!")
    
  def TftpDownload(self):
    cmd = PyShellCommand(f"run update_img".encode(), res_pattern=r"(done)\s*Bytes transferred", timeout=900, repeat_cnt=1)
    if not self.ExecuteCmd(cmd):
      raise BurnError("Update image failed!")

  def ExecuteCmd(self, cmd:PyShellCommand, skip_flush = False, skip_line_match = False):
    """
    如果操作不及时，有可能打断不了，同时此处可能要多匹配几次
    """
    logger.info(f"Execute command {cmd.cmd}...")
    if not skip_flush:
      if not self.ser.timeout or self.ser.timeout < 0:
        self.ser.timeout = 0.1
      self.ser.flush()
      self.ser.readlines()
    if cmd.delay:
      logger.info(f"Sleep for {cmd.delay} secs...")
      time.sleep(cmd.delay)
    start = time.perf_counter()
    recv_buffer = ""
    while True:
      try:
        if time.perf_counter() - start > cmd.timeout:
          logger.error(f"Execute command {cmd.cmd} timeout {cmd.timeout}!Ouput is dumped below:")
          logger.error(f"{recv_buffer}")
          logger.error("===============================================================")
          return False
        # 这个地方一直执行....
        if cmd.repeat_cnt and cmd.repeat_cnt>0:
          self.ser.write(cmd.cmd + self.cmd_padding)
          cmd.repeat_cnt -= 1
        elif cmd.repeat_cnt  < 0:
          self.ser.write(cmd.cmd + self.cmd_padding)
        try:
          line = self.ser.readline().decode()
        except UnicodeDecodeError:
          continue
        if not line:
          continue
        # 匹配到结果
        r= True
        # 跳过
        if not skip_line_match:
          r = self.pattern.match(line)
        # 没有到结尾
        if not r:
          recv_buffer = recv_buffer + line
          if not self.block_uboot_cmd_out:
            # 使用flush 貌似会多打一行
            print(line.rstrip(), flush=True)
          else:
            print("+", end="",flush=True)
          continue
        # 找到了，退出...
        print("Job finish!")
        if not cmd.res_pattern:
          logger.info(f"Command {cmd.cmd} finsihed!")
          return True 
        # 拼接完了一次性match, 应该还可以
        grp = re.search(cmd.res_pattern, recv_buffer)
        if grp is not None:
          logger.info(f"Result check passed! Command {cmd.cmd} finsihed!")
          # this should not be printed.
          # logger.info(recv_buffer)
          if not cmd.repeat_cnt:
            return True
          else:
            continue
        logger.error(f"Failed to match result of {cmd.cmd}, output dumps below:")
        logger.error(f"{recv_buffer}")
        logger.error("=============================================================")
        return False
      except serial.SerialTimeoutException:
        continue
      except Exception as e:
        logger.error(f"Got unhandled exception!")
        logger.info(traceback.format_exc())
        raise
  
  def PortInit(self, port:str):
    self.ser = serial.Serial(port, self.baudrate)
    self.ser.timeout = 0.1
  
  def DoRebootTest(self, port):
    self.PortInit(port)
    cmd = PyShellCommand(f"reboot".encode(), "", timeout=30, repeat_cnt=1, delay=0)
    cmd_r = PyShellCommand(f"\r\n".encode(), "", timeout=1, repeat_cnt=1, delay=0)
    # if not self.ExecuteCmd(cmd):
    #   raise BurnError(f"Failed to execute command {cmd.cmd}!")
    # return
    # start = time.perf_counter()
    while True:
      self.ExecuteCmd(cmd_r)
      if not self.ExecuteCmd(cmd, False, True):
        raise BurnError(f"Failed to execute command {cmd.cmd}!")
      cmd_r.repeat_cnt = 1
      cmd.repeat_cnt = 1
      start = time.perf_counter()
      while time.perf_counter() - start <= 30:
        contents = self.ser.readlines()
        for c in contents:
          logger.info(c)
        # logger.info(self.ser.readlines())
        time.sleep(1)
  
  def StartBurn(self, time_str, **kwargs):
    port = kwargs.get("port")
    reboot = kwargs.get("reboot", False)
    if reboot:
      self.DoRebootTest(port)
      return
    sip = kwargs.get("server_ip")
    bip = kwargs.get("board_ip")
    gw = kwargs.get("gateway")
    do_erase = kwargs.get("erase", False)
    # real time blocking.
    self.block_uboot_cmd_out = kwargs.get("block_output", False)
    cmds = PyShellCommand.ParseCommands("scripts.xml", **kwargs)
    if not cmds:
      raise BurnError("Fatal, invalid script file!")
    model = AliasCheck(kwargs.get("model"), MODEL_NAME_ALIAS)
    if not model or cmds.get(model, None) is None:
      raise BurnError(f"Model {kwargs.get("model")} is not supprted!")
    cmds = cmds[model]
    logger.info("Will run:")
    for cmd in cmds:
      logger.info(cmd.cmd)
    tftp_path = kwargs.get("tftp_path")
    if tftp_path is None or tftp_path == "":
      tftp_path = "images"
    tftp_path = os.path.abspath(tftp_path)
    if not os.path.exists(tftp_path):
      raise BurnError(f"{tftp_path} does not exist!")
    self.PortInit(port)
    self.HandshakeWithUboot()
    if do_erase:
      cmd = PyShellCommand(f"mmc erase 0 0x1000".encode(), "", timeout=5, repeat_cnt=1, delay=0)
      if not self.ExecuteCmd(cmd):
        raise BurnError("Failed to erase uboot!")
      logger.info("Erase uboot successfully!")
      return
    # self.SetNetWork(sip, bip, gw)
    tq = Queue()
    proc = Process(target=PyTftpServer.ProcessEntry, args=(tftp_path,  f"log/TFTP_at_{time_str}.log", tq))
    proc.start()
    rsp = None
    def cb_sig_int(sig, frame):
      logger.info("Terminated!")
      proc.kill()
      sys.exit(-1)
    try:
      signal.signal(signal.SIGINT, cb_sig_int)
      rsp = tq.get(timeout=2)
      if rsp == PyTftpServer.FATAL_CODE:
        logger.error("Create tftp may failed, please make sure:")
        logger.error("1. Filewall is turned off or programs are allowed to use UDP/69")
        logger.error("2. There isn't any other process using UDP/69")
        logger.error("3. TFTP workpath exists and nvp_burn has the rw permissions.")
        raise BurnError("Start tftp failed!")
      for cmd in cmds:
        cmd:UbootCommand
        if not self.ExecuteCmd(cmd):
          raise BurnError(f"Failed to execute command {cmd.cmd}!")
        if cmd.done_delay:
          logger.info(f"Wait cmd {cmd.cmd} finish its job for {cmd.done_delay} secs.")
          time.sleep(cmd.done_delay)
    except queue.Empty:
      logger.error("Failed to create tftp!")
      raise
    except:
      raise
    finally:
      signal.signal(signal.SIGINT, signal.SIG_DFL)
      tq.put("Quit")
      proc.join()
    logger.info("Image update successfully!")
  